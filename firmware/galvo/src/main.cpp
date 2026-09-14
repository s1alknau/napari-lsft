#define IS_XIAO 1
#include <Arduino.h>
#include <stdio.h>
#include <string.h>
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include <arpa/inet.h>
#include <vector>
#include "SPIRenderer.h"
#include "esp_task_wdt.h"
#include <ArduinoJson.h>
#include <Preferences.h>

static const char *TAG = "main";

// Preferences object for persistent storage
Preferences preferences;

// Simple serial buffer
String incomingData;

// Binary protocol state
enum BinaryProtocolState {
  BINARY_IDLE,
  BINARY_RECEIVING_HEADER,
  BINARY_RECEIVING_DATA
};

BinaryProtocolState binaryState = BINARY_IDLE;
uint16_t binaryExpectedPoints = 0;
uint16_t binaryReceivedPoints = 0;
uint8_t binaryBuffer[4];  // Buffer for one point (2 bytes X + 2 bytes Y)
uint8_t binaryBufferIndex = 0;

// Renderer pointer
SPIRenderer *renderer = nullptr;

// Default parameters
int X_MIN = 0;
int X_MAX = 2048;
int Y_MIN = 0;
int Y_MAX = 2048;
int X_OFFSET = 0;  // X-axis offset
int Y_OFFSET = 0;  // Y-axis offset
int STEP_X = 20;   // Step size for X axis
int STEP_Y = 20;   // Step size for Y axis
int tPixelDwelltime = 0;
int nFrames = 10;
bool SNAKE = false; // Snake scanning pattern (alternate line direction)
bool SIM = false;   // Structured illumination mode (shifts pattern by STEP_Y/nFrames per frame)
bool ENABLE_TRIG_FRAME = true;  // Enable frame trigger
bool ENABLE_TRIG_LINE = true;   // Enable line trigger
bool ENABLE_TRIG_PIXEL = true;  // Enable pixel trigger

// Single point positioning mode
bool SINGLE = false;  // Single point mode (stationary position)
int X_POS = 2048;     // X position for single point mode (0-4095)
int Y_POS = 2048;     // Y position for single point mode (0-4095)

extern "C"
{
  void app_main(void);
}

// -------------------------------------------------------------------
// HELPER: Save parameters to preferences
// -------------------------------------------------------------------
void saveParameters() {
  preferences.begin("galvo", false);
  preferences.putInt("X_MIN", X_MIN);
  preferences.putInt("X_MAX", X_MAX);
  preferences.putInt("Y_MIN", Y_MIN);
  preferences.putInt("Y_MAX", Y_MAX);
  preferences.putInt("X_OFFSET", X_OFFSET);
  preferences.putInt("Y_OFFSET", Y_OFFSET);
  preferences.putInt("STEP_X", STEP_X);
  preferences.putInt("STEP_Y", STEP_Y);
  preferences.putInt("tPixelDwell", tPixelDwelltime);
  preferences.putInt("nFrames", nFrames);
  preferences.putBool("SNAKE", SNAKE);
  preferences.putBool("SIM", SIM);
  preferences.putBool("TRIG_FRAME", ENABLE_TRIG_FRAME);
  preferences.putBool("TRIG_LINE", ENABLE_TRIG_LINE);
  preferences.putBool("TRIG_PIXEL", ENABLE_TRIG_PIXEL);
  preferences.putBool("SINGLE", SINGLE);
  preferences.putInt("X_POS", X_POS);
  preferences.putInt("Y_POS", Y_POS);
  preferences.end();
  ESP_LOGI(TAG, "Parameters saved to preferences");
}

// -------------------------------------------------------------------
// HELPER: Load parameters from preferences
// -------------------------------------------------------------------
void loadParameters() {
  preferences.begin("galvo", true); // read-only mode
  X_MIN = preferences.getInt("X_MIN", 0);
  X_MAX = preferences.getInt("X_MAX", 2048);
  Y_MIN = preferences.getInt("Y_MIN", 0);
  Y_MAX = preferences.getInt("Y_MAX", 2048);
  X_OFFSET = preferences.getInt("X_OFFSET", 0);
  Y_OFFSET = preferences.getInt("Y_OFFSET", 0);
  STEP_X = preferences.getInt("STEP_X", 8);
  STEP_Y = preferences.getInt("STEP_Y", 8);
  tPixelDwelltime = preferences.getInt("tPixelDwell", 0);
  nFrames = preferences.getInt("nFrames", 10);
  SNAKE = preferences.getBool("SNAKE", false);
  SIM = preferences.getBool("SIM", false);
  ENABLE_TRIG_FRAME = preferences.getBool("TRIG_FRAME", true);
  ENABLE_TRIG_LINE = preferences.getBool("TRIG_LINE", true);
  ENABLE_TRIG_PIXEL = preferences.getBool("TRIG_PIXEL", true);
  SINGLE = preferences.getBool("SINGLE", false);
  X_POS = preferences.getInt("X_POS", 2048);
  Y_POS = preferences.getInt("Y_POS", 2048);
  preferences.end();
  ESP_LOGI(TAG, "Parameters loaded from preferences: X_MIN=%d X_MAX=%d Y_MIN=%d Y_MAX=%d X_OFF=%d Y_OFF=%d STEP_X=%d STEP_Y=%d tPixelDwell=%d nFrames=%d SNAKE=%d SIM=%d TRIG_F=%d TRIG_L=%d TRIG_P=%d SINGLE=%d X_POS=%d Y_POS=%d",
           X_MIN, X_MAX, Y_MIN, Y_MAX, X_OFFSET, Y_OFFSET, STEP_X, STEP_Y, tPixelDwelltime, nFrames, SNAKE, SIM, ENABLE_TRIG_FRAME, ENABLE_TRIG_LINE, ENABLE_TRIG_PIXEL, SINGLE, X_POS, Y_POS);
}


// -------------------------------------------------------------------
// HELPER: Handle JSON commands
// -------------------------------------------------------------------
void handleJSON(const String &jsonString) {
  StaticJsonDocument<512> doc;
  DeserializationError error = deserializeJson(doc, jsonString);
  if (error) {
    Serial.println("{\"status\":\"error\",\"info\":\"JSON parse failed\"}");
    return;
  }

  const char* task = doc["task"];
  if (!task) {
    Serial.println("{\"status\":\"error\",\"info\":\"Missing task\"}");
    return;
  }

  // Handle /state_get command
  if (strcmp(task, "/state_get") == 0) {
    // {"task":"/state_get", "qid":1}
    int qid = doc["qid"] | 0;
    Serial.print("++\n{\"identifier_name\":\"UC2_GalvoScanner\",");
    Serial.print("\"identifier_id\":\"V1.0\",");
    Serial.print("\"identifier_date\":\"");
    Serial.print(__DATE__);
    Serial.print(" ");
    Serial.print(__TIME__);
    Serial.print("\",");
    Serial.print("\"identifier_author\":\"UC2\",");
    Serial.print("\"IDENTIFIER_NAME\":\"uc2-esp\",");
    Serial.print("\"configIsSet\":0,");
    Serial.print("\"pindef\":\"UC2\",");
    Serial.print("\"success\":1");
    if (qid != 0) {
      Serial.print(",\"qid\":");
      Serial.print(qid);
    }
    Serial.println("}\n--");
    return;
  }

  // Handle /galvo_act command
  if (strcmp(task, "/galvo_act") == 0) {
    /*_
    {"task":"/galvo_act", "qid":1, "X_MIN":0, "X_MAX":2048, "Y_MIN":0, "Y_MAX":2048, "STEP_X":10, "STEP_Y":100, "tPixelDwelltime":0, "nFrames":1, "SNAKE":true}
    {"task":"/galvo_act", "qid":1, "X_POS":1000, "Y_POS":2048, "SINGLE":true}
    */

    int qid = doc["qid"] | 0;

    // Check if SINGLE mode is being set
    if (doc.containsKey("SINGLE")) {
      bool newSingle = doc["SINGLE"];
      SINGLE = newSingle;

      if (SINGLE) {
        // In single mode, get X_POS and Y_POS
        X_POS = doc["X_POS"] | X_POS;
        Y_POS = doc["Y_POS"] | Y_POS;

        // Clamp to valid DAC range
        X_POS = (X_POS < 0) ? 0 : ((X_POS > 4095) ? 4095 : X_POS);
        Y_POS = (Y_POS < 0) ? 0 : ((Y_POS > 4095) ? 4095 : Y_POS);

        // Save parameters
        saveParameters();

        // Report success
        Serial.print("++\n{\"task\":\"/galvo_act\",\"status\":\"success\",\"mode\":\"single\",\"X_POS\":");
        Serial.print(X_POS);
        Serial.print(",\"Y_POS\":");
        Serial.print(Y_POS);
        if (qid != 0) {
          Serial.print(",\"qid\":");
          Serial.print(qid);
        }
        Serial.println("}\n--");
        return;
      }
    }

    // Get parameters from JSON, use current values as defaults
    int newXMin = doc["X_MIN"] | X_MIN;
    int newXMax = doc["X_MAX"] | X_MAX;
    int newYMin = doc["Y_MIN"] | Y_MIN;
    int newYMax = doc["Y_MAX"] | Y_MAX;
    int newXOffset = doc["X_OFFSET"] | X_OFFSET;
    int newYOffset = doc["Y_OFFSET"] | Y_OFFSET;

    // Handle both STEP (backward compatibility) and STEP_X/STEP_Y
    int newStepX, newStepY;
    if (doc.containsKey("STEP")) {
      int stepValue = doc["STEP"];
      newStepX = stepValue;
      newStepY = stepValue;
    } else {
      newStepX = doc["STEP_X"] | STEP_X;
      newStepY = doc["STEP_Y"] | STEP_Y;
      Serial.println("Using STEP_X and STEP_Y:");
      Serial.print("STEP_X: "); Serial.println(newStepX);
      Serial.print("STEP_Y: "); Serial.println(newStepY);
    }

    int newDwell = doc["tPixelDwelltime"] | tPixelDwelltime;
    int newFrames = doc["nFrames"] | nFrames;
    bool newSnake = doc["SNAKE"] | SNAKE;
    bool newSim = doc["SIM"] | SIM;
    bool newTrigFrame = doc["ENABLE_TRIG_FRAME"] | ENABLE_TRIG_FRAME;
    bool newTrigLine = doc["ENABLE_TRIG_LINE"] | ENABLE_TRIG_LINE;
    bool newTrigPixel = doc["ENABLE_TRIG_PIXEL"] | ENABLE_TRIG_PIXEL;

    // If SINGLE wasn't explicitly set to false, keep scanning mode
    if (!doc.containsKey("SINGLE")) {
      SINGLE = false;  // Default to scanning mode when setting scan parameters
    }

    // Update global parameters
    X_MIN = newXMin;
    X_MAX = newXMax;
    Y_MIN = newYMin;
    Y_MAX = newYMax;
    X_OFFSET = newXOffset;
    Y_OFFSET = newYOffset;
    STEP_X = newStepX;
    STEP_Y = newStepY;
    tPixelDwelltime = newDwell;
    nFrames = newFrames;
    SNAKE = newSnake;
    SIM = newSim;
    ENABLE_TRIG_FRAME = newTrigFrame;
    ENABLE_TRIG_LINE = newTrigLine;
    ENABLE_TRIG_PIXEL = newTrigPixel;

    // Save parameters to preferences
    saveParameters();

    // Update renderer if it exists
    if (renderer != nullptr) {
      renderer->setParameters(X_MIN, X_MAX, Y_MIN, Y_MAX, X_OFFSET, Y_OFFSET, STEP_X, STEP_Y,
                              tPixelDwelltime, nFrames, SNAKE, SIM,
                              ENABLE_TRIG_FRAME, ENABLE_TRIG_LINE, ENABLE_TRIG_PIXEL);
    }

    // Report success
    Serial.print("++\n{\"task\":\"/galvo_act\",\"status\":\"success\"");
    if (qid != 0) {
      Serial.print(",\"qid\":");
      Serial.print(qid);
    }
    Serial.println("}\n--");
    return;
  }

  // Handle /galvo_get command
  // {"task":"/galvo_get", "qid":1}
  if (strcmp(task, "/galvo_get") == 0) {
    int qid = doc["qid"] | 0;
    Serial.print("++\n{\"task\":\"/galvo_get\",");
    Serial.print("\"X_MIN\":");
    Serial.print(X_MIN);
    Serial.print(",\"X_MAX\":");
    Serial.print(X_MAX);
    Serial.print(",\"Y_MIN\":");
    Serial.print(Y_MIN);
    Serial.print(",\"Y_MAX\":");
    Serial.print(Y_MAX);
    Serial.print(",\"X_OFFSET\":");
    Serial.print(X_OFFSET);
    Serial.print(",\"Y_OFFSET\":");
    Serial.print(Y_OFFSET);
    Serial.print(",\"STEP_X\":");
    Serial.print(STEP_X);
    Serial.print(",\"STEP_Y\":");
    Serial.print(STEP_Y);
    Serial.print(",\"tPixelDwelltime\":");
    Serial.print(tPixelDwelltime);
    Serial.print(",\"nFrames\":");
    Serial.print(nFrames);
    Serial.print(",\"SNAKE\":");
    Serial.print(SNAKE ? "true" : "false");
    Serial.print(",\"SIM\":");
    Serial.print(SIM ? "true" : "false");
    Serial.print(",\"SINGLE\":");
    Serial.print(SINGLE ? "true" : "false");
    Serial.print(",\"X_POS\":");
    Serial.print(X_POS);
    Serial.print(",\"Y_POS\":");
    Serial.print(Y_POS);
    Serial.print(",\"ENABLE_TRIG_FRAME\":");
    Serial.print(ENABLE_TRIG_FRAME ? "true" : "false");
    Serial.print(",\"ENABLE_TRIG_LINE\":");
    Serial.print(ENABLE_TRIG_LINE ? "true" : "false");
    Serial.print(",\"ENABLE_TRIG_PIXEL\":");
    Serial.print(ENABLE_TRIG_PIXEL ? "true" : "false");
    Serial.print(",\"success\":1");
    if (qid != 0) {
      Serial.print(",\"qid\":");
      Serial.print(qid);
    }
    Serial.println("}\n--");
    return;
  }

  // Handle /pointcloud_clear command
  if (strcmp(task, "/pointcloud_clear") == 0) {
    int qid = doc["qid"] | 0;
    if (renderer != nullptr) {
      renderer->clearPointCloud();
      Serial.print("++\n{\"task\":\"/pointcloud_clear\",\"status\":\"success\"");
      if (qid != 0) {
        Serial.print(",\"qid\":");
        Serial.print(qid);
      }
      Serial.println("}\n--");
    } else {
      Serial.println("{\"status\":\"error\",\"info\":\"Renderer not initialized\"}");
    }
    return;
  }

  // Handle /pointcloud_render command
  if (strcmp(task, "/pointcloud_render") == 0) {
    int qid = doc["qid"] | 0;
    if (renderer != nullptr) {
      renderer->renderPointCloud();
      Serial.print("++\n{\"task\":\"/pointcloud_render\",\"status\":\"success\"");
      if (qid != 0) {
        Serial.print(",\"qid\":");
        Serial.print(qid);
      }
      Serial.println("}\n--");
    } else {
      Serial.println("{\"status\":\"error\",\"info\":\"Renderer not initialized\"}");
    }
    return;
  }

  // Handle /pointcloud_start command - initiates binary transfer
  if (strcmp(task, "/pointcloud_start") == 0) {
    /*
    Binary transfer protocol:
    1. Send JSON: {"task":"/pointcloud_start", "numPoints":1000, "qid":1}
    2. ESP responds with ready
    3. Send binary data: [X1_low, X1_high, Y1_low, Y1_high, X2_low, X2_high, Y2_low, Y2_high, ...]
       Each coordinate is 16-bit little-endian (0-4095)
    4. ESP responds when complete
    */
    int qid = doc["qid"] | 0;
    int numPoints = doc["numPoints"] | 0;
    
    if (numPoints <= 0 || numPoints > 10000) {
      Serial.println("{\"status\":\"error\",\"info\":\"Invalid numPoints (must be 1-10000)\"}");
      return;
    }
    
    if (renderer == nullptr) {
      Serial.println("{\"status\":\"error\",\"info\":\"Renderer not initialized\"}");
      return;
    }
    
    // Clear existing point cloud
    renderer->clearPointCloud();
    
    // Set up binary reception
    binaryState = BINARY_RECEIVING_DATA;
    binaryExpectedPoints = numPoints;
    binaryReceivedPoints = 0;
    binaryBufferIndex = 0;
    
    // Send ready response
    Serial.print("++\n{\"task\":\"/pointcloud_start\",\"status\":\"ready\",\"numPoints\":");
    Serial.print(numPoints);
    if (qid != 0) {
      Serial.print(",\"qid\":");
      Serial.print(qid);
    }
    Serial.println("}\n--");
    Serial.flush();  // Ensure response is sent before binary data arrives
    return;
  }

  // Unknown task
  Serial.println("{\"status\":\"error\",\"info\":\"Unknown task\"}");
}

// -------------------------------------------------------------------
// HELPER: Process binary point cloud data
// -------------------------------------------------------------------
void processBinaryData(uint8_t byte) {
  if (binaryState != BINARY_RECEIVING_DATA) {
    return;
  }
  
  // Accumulate bytes for one point (4 bytes: X_low, X_high, Y_low, Y_high)
  binaryBuffer[binaryBufferIndex++] = byte;
  
  if (binaryBufferIndex >= 4) {
    // We have a complete point
    uint16_t x = (binaryBuffer[1] << 8) | binaryBuffer[0];  // Little-endian
    uint16_t y = (binaryBuffer[3] << 8) | binaryBuffer[2];  // Little-endian
    
    // Add point to renderer
    if (renderer != nullptr) {
      renderer->addPoint(x, y);
    }
    
    binaryReceivedPoints++;
    binaryBufferIndex = 0;
    
    // Check if we've received all points
    if (binaryReceivedPoints >= binaryExpectedPoints) {
      // Binary transfer complete
      Serial.print("++\n{\"task\":\"/pointcloud_start\",\"status\":\"complete\",\"receivedPoints\":");
      Serial.print(binaryReceivedPoints);
      Serial.println("}\n--");
      
      // Reset binary state
      binaryState = BINARY_IDLE;
      binaryExpectedPoints = 0;
      binaryReceivedPoints = 0;
      binaryBufferIndex = 0;
    }
  }
}

// -------------------------------------------------------------------
// HELPER: Process serial input
// -------------------------------------------------------------------
void processSerial() {
  while (Serial.available()) {
    uint8_t byte = Serial.read();
    
    // Check if we're in binary mode
    if (binaryState == BINARY_RECEIVING_DATA) {
      processBinaryData(byte);
    } else {
      // Normal JSON mode
      char c = (char)byte;
      if (c == '\n') {
        // Process one line of JSON
        if (incomingData.length() > 0) {
          handleJSON(incomingData);
          incomingData = "";
        }
      } else if (c != '\r') {
        incomingData += c;
      }
    }
  }
}


void app_main()
{
  esp_log_level_set("gpio", ESP_LOG_WARN);
  ESP_LOGD(TAG, "Starting up...");

  // Initialize Serial
  Serial.begin(115200);

  // Load parameters from preferences
  loadParameters();

  // Disable the task watchdog for the main task
  esp_task_wdt_delete(xTaskGetIdleTaskHandleForCPU(0));

  // Build the renderer from the parameters loadParameters() just restored, and
  // assign it to the *global* `renderer` declared at the top of this file:
  // processSerial() pushes every /galvo_act through that pointer, so a local
  // one here would shadow it and leave the global at nullptr -- parameter
  // updates and the point-cloud commands would silently do nothing.
  renderer = new SPIRenderer(X_MIN, X_MAX, Y_MIN, Y_MAX, X_OFFSET, Y_OFFSET,
                             STEP_X, STEP_Y, tPixelDwelltime, nFrames,
                             SNAKE, SIM,
                             ENABLE_TRIG_FRAME, ENABLE_TRIG_LINE,
                             ENABLE_TRIG_PIXEL);

  while (1) {
    // Process any incoming serial commands
    processSerial();

    if (SINGLE) {
      // In SINGLE mode, set galvos to stationary position
      renderer->setSinglePosition(X_POS, Y_POS);
      // Longer delay in single mode since we're not scanning
      vTaskDelay(pdMS_TO_TICKS(100));
    } else {
      // Render one frame (allows serial processing between frames)
      renderer->start();
      // One tick, not ten: at 10 ms the loop -- not the scan -- set the sweep
      // rate, capping it near 30 Hz. A light sheet wants a few hundred sweeps
      // per second, and so does anyone trying to *hear* whether the galvo
      // moves. One tick still lets the idle task run and feeds the watchdog.
      vTaskDelay(1);
    }
  }
}
