#include <cstring>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "SPIRenderer.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "driver/timer.h"
#include "esp_err.h"
#include "esp_log.h"

// For fast GPIO toggling on ESP32-S3:
#include "soc/gpio_struct.h" // Gives you GPIO.out_w1ts, etc.
#include "hal/gpio_types.h"
#include "esp_rom_sys.h" // For ets_delay_us()

static const char *TAG = "SPIRenderer";

// Comment/uncomment if you want to compile for XIAO or not
// #define IS_XIAO

////////////////////////////////////////////////////////////////
// Helper: Fast toggling of GPIO pins
////////////////////////////////////////////////////////////////
#ifndef IS_XIAO
static inline void fast_gpio_set(int pin)
{
  // For GPIO pin < 32 on ESP32-S3:
  GPIO.out_w1ts = (1U << pin);
}

static inline void fast_gpio_clear(int pin)
{
  // For GPIO pin < 32 on ESP32-S3:
  GPIO.out_w1tc = (1U << pin);
}
#endif

////////////////////////////////////////////////////////////////
// Set pixel/line/frame trigger pins
////////////////////////////////////////////////////////////////

void set_gpio_pins(int pixelTrigVal, int lineTrigVal, int frameTrigVal)
{
  uint32_t gpio_mask = ((1ULL << PIN_NUM_TRIG_PIXEL) |
                        (1ULL << PIN_NUM_TRIG_LINE) |
                        (1ULL << PIN_NUM_TRIG_FRAME));

  gpio_config_t io_conf;
  io_conf.intr_type = GPIO_INTR_DISABLE;
  io_conf.mode = GPIO_MODE_OUTPUT;
  io_conf.pin_bit_mask = gpio_mask;
  io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
  io_conf.pull_up_en = GPIO_PULLUP_DISABLE;
  gpio_config(&io_conf);

#ifdef IS_XIAO
  if (pixelTrigVal)
  {
    gpio_set_level(PIN_NUM_TRIG_PIXEL, 1);
  }
  else
  {
    gpio_set_level(PIN_NUM_TRIG_PIXEL, 0);
  }

  if (lineTrigVal)
  {
    gpio_set_level(PIN_NUM_TRIG_LINE, 1);
  }
  else
  {
    gpio_set_level(PIN_NUM_TRIG_LINE, 0);
  }

  if (frameTrigVal)
  {
    gpio_set_level(PIN_NUM_TRIG_FRAME, 1);
  }
  else
  {
    gpio_set_level(PIN_NUM_TRIG_FRAME, 0);
  }
#else
  if (pixelTrigVal)
    GPIO.out_w1ts = (1ULL << PIN_NUM_TRIG_PIXEL);
  else
    GPIO.out_w1tc = (1ULL << PIN_NUM_TRIG_PIXEL);

  if (lineTrigVal)
    GPIO.out_w1ts = (1ULL << PIN_NUM_TRIG_LINE);
  else
    GPIO.out_w1tc = (1ULL << PIN_NUM_TRIG_LINE);

  if (frameTrigVal)
    GPIO.out_w1ts = (1ULL << PIN_NUM_TRIG_FRAME);
  else
    GPIO.out_w1tc = (1ULL << PIN_NUM_TRIG_FRAME);
#endif
}

////////////////////////////////////////////////////////////////
// Trigger camera for tPixelDwelltime microseconds
////////////////////////////////////////////////////////////////
void trigger_camera(int tPixelDwelltime, int triggerPin = PIN_NUM_TRIG_PIXEL)
{
#ifdef IS_XIAO
  gpio_set_level((gpio_num_t)triggerPin, 1);
  ets_delay_us(tPixelDwelltime);
  gpio_set_level((gpio_num_t)triggerPin, 0);
#else
  // On ESP32-S3, optionally do direct register toggling
  GPIO.out_w1ts = (1U << triggerPin); // set bit
  ets_delay_us(tPixelDwelltime);
  GPIO.out_w1tc = (1U << triggerPin); // clear bit
#endif
}

////////////////////////////////////////////////////////////////
// The SPIRenderer class
////////////////////////////////////////////////////////////////
SPIRenderer::SPIRenderer(int xmin, int xmax, int ymin, int ymax, int xoffset, int yoffset,
                         int stepx, int stepy, int tPixelDwelltime, int nFramesI, bool snake, bool sim,
                         bool enableTrigFrame, bool enableTrigLine, bool enableTrigPixel)
{
  // Avoid variable shadowing:
  this->tPixelDwelltime = tPixelDwelltime;

  // The number of steps in x and y
  nX = (xmax - xmin) / stepx;
  nY = (ymax - ymin) / stepy;

  X_MIN = xmin;
  X_MAX = xmax;
  Y_MIN = ymin;
  Y_MAX = ymax;
  X_OFFSET = xoffset;
  Y_OFFSET = yoffset;
  STEP_X = stepx;
  STEP_Y = stepy;
  nFrames = nFramesI;
  SNAKE = snake;
  SIM = sim;
  ENABLE_TRIG_FRAME = enableTrigFrame;
  ENABLE_TRIG_LINE = enableTrigLine;
  ENABLE_TRIG_PIXEL = enableTrigPixel;

  printf("Setting up renderer with parameters: X[%d,%d] Y[%d,%d] OFF[%d,%d] STEP[%d,%d] dwell:%d frames:%d SNAKE:%d SIM:%d TRIG[F:%d L:%d P:%d]\n",
         xmin, xmax, ymin, ymax, xoffset, yoffset, stepx, stepy, tPixelDwelltime, nFrames, snake, sim,
         enableTrigFrame, enableTrigLine, enableTrigPixel);

  // Set up the laser pin
  gpio_set_direction((gpio_num_t)PIN_NUM_LASER, GPIO_MODE_OUTPUT);
  // Set up trigger pins
  gpio_set_direction((gpio_num_t)PIN_NUM_TRIG_PIXEL, GPIO_MODE_OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_NUM_TRIG_FRAME, GPIO_MODE_OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_NUM_TRIG_LINE, GPIO_MODE_OUTPUT);

  // Set up the LDAC output
  gpio_set_direction((gpio_num_t)PIN_NUM_LDAC, GPIO_MODE_OUTPUT);

  // Quick test of the LDAC pin
  for (int i = 0; i < 2; i++)
  {
    gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 0);
    vTaskDelay(pdMS_TO_TICKS(1000));
    gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 1);
    vTaskDelay(pdMS_TO_TICKS(1000));
  }

  // SPI bus configuration
  // MCP4822-E/SN
  esp_err_t ret;
  spi_bus_config_t buscfg = {
      .mosi_io_num = PIN_NUM_SDI,
      .miso_io_num = PIN_NUM_MISO,
      .sclk_io_num = PIN_NUM_SCK,
      .quadwp_io_num = -1,
      .quadhd_io_num = -1,
      .max_transfer_sz = 4096};

  spi_device_interface_config_t devcfg = {
      .command_bits = 0,
      .address_bits = 0,
      .dummy_bits = 0,
      .mode = 0,
      // 20 MHz is the MCP4822's absolute maximum and leaves no margin for
      // trace capacitance or a 3.3 V drive into a 5 V-powered part. Each
      // transfer is only 16 bits, so 2 MHz costs ~7 us per pixel pair and
      // buys a lot of reliability.
      .clock_speed_hz = 2 * 1000 * 1000,
      .spics_io_num = PIN_NUM_CS,
      .flags = SPI_DEVICE_NO_DUMMY,
      .queue_size = 2,
  };

#ifdef IS_XIAO
  ret = spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO);
  ESP_ERROR_CHECK(ret);

  ret = spi_bus_add_device(SPI3_HOST, &devcfg, &spi);
  ESP_ERROR_CHECK(ret);
#else
  ret = spi_bus_initialize(HSPI_HOST, &buscfg, 1);
  ESP_ERROR_CHECK(ret);

  ret = spi_bus_add_device(HSPI_HOST, &devcfg, &spi);
  ESP_ERROR_CHECK(ret);
#endif

  // If you want chip-select forced low all the time (not typically recommended),
  // you can do:
  gpio_set_level((gpio_num_t)PIN_NUM_CS, 0);

  printf("SPI Bus init ret = %s\n", esp_err_to_name(ret));
}

void SPIRenderer::setParameters(int xmin, int xmax, int ymin, int ymax, int xoffset, int yoffset,
                                int stepx, int stepy, int tPixelDwelltime, int nFramesI, bool snake, bool sim,
                                bool enableTrigFrame, bool enableTrigLine, bool enableTrigPixel)
{
  // Again, fix shadowing
  this->tPixelDwelltime = tPixelDwelltime;

  nX = (xmax - xmin) / stepx;
  nY = (ymax - ymin) / stepy;
  X_MIN = xmin;
  X_MAX = xmax;
  Y_MIN = ymin;
  Y_MAX = ymax;
  X_OFFSET = xoffset;
  Y_OFFSET = yoffset;
  STEP_X = stepx;
  STEP_Y = stepy;
  nFrames = nFramesI;
  SNAKE = snake;
  SIM = sim;
  ENABLE_TRIG_FRAME = enableTrigFrame;
  ENABLE_TRIG_LINE = enableTrigLine;
  ENABLE_TRIG_PIXEL = enableTrigPixel;

  printf("Setting parameters: X[%d,%d] Y[%d,%d] OFF[%d,%d] STEP[%d,%d] dwell:%d frames:%d SNAKE:%d SIM:%d TRIG[F:%d L:%d P:%d]\n",
         xmin, xmax, ymin, ymax, xoffset, yoffset, stepx, stepy, tPixelDwelltime, nFrames, snake, sim,
         enableTrigFrame, enableTrigLine, enableTrigPixel);
}

////////////////////////////////////////////////////////////////
// Fast GPIO helpers
////////////////////////////////////////////////////////////////
static inline void fast_set(int pin) { GPIO.out_w1ts = (1U << pin); }
static inline void fast_clear(int pin) { GPIO.out_w1tc = (1U << pin); }

static inline void trigger_pulse(int pin, int pulse_us)
{
  fast_set(pin);
  esp_rom_delay_us(pulse_us);
  fast_clear(pin);
}

/*
void SPIRenderer::drawFrame()
{
  ESP_LOGD(TAG, "Drawing frame %d / %d", currentFrame + 1, nFrames);

  // ---- FRAME CLOCK ----
  if (ENABLE_TRIG_FRAME)
    trigger_pulse(PIN_NUM_TRIG_FRAME, 0); // short pulse

  // Optional SIM Y offset
  int simYOffset = (SIM && nFrames > 0) ? (STEP_Y * currentFrame) / nFrames : 0;
  int lineNumber = 0;

  for (int dacX = X_MIN; dacX <= X_MAX; dacX += STEP_X)
  {
    // ---- LINE CLOCK ----
    if (ENABLE_TRIG_LINE)
      trigger_pulse(PIN_NUM_TRIG_LINE, 0); // one pulse per line

    // Determine Y scan direction for snake pattern
    int yStart, yEnd, yStep;
    if ((SNAKE || SIM) && (lineNumber % 2 == 1))
    {
      yStart = Y_MAX;
      yEnd = Y_MIN;
      yStep = -STEP_Y;
    }
    else
    {
      yStart = Y_MIN;
      yEnd = Y_MAX;
      yStep = STEP_Y;
    }

    for (int dacY = yStart; (yStep > 0) ? (dacY <= yEnd) : (dacY >= yEnd); dacY += yStep)
    {
      // Apply offsets
      int dacXw = dacX + X_OFFSET;
      int dacYw = dacY + Y_OFFSET + simYOffset;
      dacXw = (dacXw < 0) ? 0 : ((dacXw > 4095) ? 4095 : dacXw);
      dacYw = (dacYw < 0) ? 0 : ((dacYw > 4095) ? 4095 : dacYw);

      // SPI transaction setup
      spi_transaction_t t1 = {};
      t1.length = 16;
      t1.flags = SPI_TRANS_USE_TXDATA;
      t1.tx_data[0] = (0b00110000 | ((dacXw >> 8) & 0x0F));
      t1.tx_data[1] = (dacXw & 0xFF);

      spi_transaction_t t2 = {};
      t2.length = 16;
      t2.flags = SPI_TRANS_USE_TXDATA;
      t2.tx_data[0] = (0b10110000 | ((dacYw >> 8) & 0x0F));
      t2.tx_data[1] = (dacYw & 0xFF);

      // Update DAC
      fast_clear(PIN_NUM_LDAC);
      spi_device_polling_transmit(spi, &t1);
      spi_device_polling_transmit(spi, &t2);
      fast_set(PIN_NUM_LDAC);

      // ---- PIXEL CLOCK ----
      if (ENABLE_TRIG_PIXEL)
        trigger_pulse(PIN_NUM_TRIG_PIXEL, tPixelDwelltime);
    }

    lineNumber++;
  }

  currentFrame++;
  if (currentFrame >= nFrames)
    currentFrame = 0;
}
    */

int clamp(int &value, int minVal, int maxVal)
{
  if (value < minVal)
    value = minVal;
  if (value > maxVal)
    value = maxVal;
  return value;
}

void SPIRenderer::drawFrame()
{
  ESP_LOGD(TAG, "Drawing frame %d / %d", currentFrame + 1, nFrames);

  // Compute SIM offset
  int simYOffset = (SIM && nFrames > 0) ? (STEP_Y * currentFrame) / nFrames : 0;

  // Bitmasks for atomic trigger control
  const uint32_t PIXEL_BIT = (1U << PIN_NUM_TRIG_PIXEL);
  const uint32_t LINE_BIT = (1U << PIN_NUM_TRIG_LINE);
  const uint32_t FRAME_BIT = (1U << PIN_NUM_TRIG_FRAME);

  // ----------------------------------------------------------------------
  // FRAME PULSE (once at start of frame)
  // ----------------------------------------------------------------------
  if (ENABLE_TRIG_FRAME)
  {
    GPIO.out_w1ts = FRAME_BIT; // all bits HIGH simultaneously
    esp_rom_delay_us(5);       // short 5 µs pulse
    GPIO.out_w1tc = FRAME_BIT; // all bits LOW simultaneously
  }

  int lineNumber = 0;

  // ----------------------------------------------------------------------
  // X-loop: iterate over lines
  // ----------------------------------------------------------------------
  for (int dacX = X_MIN; dacX <= X_MAX; dacX += STEP_X)
  {
    // LINE pulse at start of each row
    if (ENABLE_TRIG_LINE)
    {
      GPIO.out_w1ts = LINE_BIT;
      esp_rom_delay_us(5);
      GPIO.out_w1tc = LINE_BIT;
    }

    // Determine Y scanning direction
    int yStart, yEnd, yStep;
    if ((SNAKE || SIM) && (lineNumber % 2 == 1))
    {
      yStart = Y_MAX;
      yEnd = Y_MIN;
      yStep = -STEP_Y;
    }
    else
    {
      yStart = Y_MIN;
      yEnd = Y_MAX;
      yStep = STEP_Y;
    }

    // ------------------------------------------------------------------
    // Y-loop: pixels in this line
    // ------------------------------------------------------------------
    for (int dacY = yStart; (yStep > 0) ? (dacY <= yEnd) : (dacY >= yEnd); dacY += yStep)
    {
      // Compute DAC positions
      int dacXw = dacX + X_OFFSET; // clamp(dacX + X_OFFSET, 0, 4095);
      int dacYw = dacY + Y_OFFSET; // clamp(dacY + Y_OFFSET + simYOffset, 0, 4095);

      spi_transaction_t t1 = {};
      t1.length = 16;
      t1.flags = SPI_TRANS_USE_TXDATA;
      t1.tx_data[0] = (0b00110000 | ((dacXw >> 8) & 0x0F));
      t1.tx_data[1] = (dacXw & 0xFF);

      spi_transaction_t t2 = {};
      t2.length = 16;
      t2.flags = SPI_TRANS_USE_TXDATA;
      t2.tx_data[0] = (0b10110000 | ((dacYw >> 8) & 0x0F));
      t2.tx_data[1] = (dacYw & 0xFF);

      // Update DACs (atomic latch)
      GPIO.out_w1tc = (1U << PIN_NUM_LDAC);
      spi_device_polling_transmit(spi, &t1);
      spi_device_polling_transmit(spi, &t2);
      GPIO.out_w1ts = (1U << PIN_NUM_LDAC);

      // --------------------------------------------------------------
      // PIXEL PULSE — synchronous, stable, and jitter-free
      // --------------------------------------------------------------
      if (ENABLE_TRIG_PIXEL)
      {
        // combine bits that should toggle together
        uint32_t mask_on = PIXEL_BIT; // only pixel goes high per dwell
        uint32_t mask_off = PIXEL_BIT;

        // atomic HIGH + dwell + atomic LOW
        GPIO.out_w1ts = mask_on;
        esp_rom_delay_us(tPixelDwelltime);
        GPIO.out_w1tc = mask_off;
      }
    }
    if(0){
    // invert Y direction for snake pattern
    yStart = yEnd;
    if ((SNAKE || SIM) && (lineNumber % 2 == 1))
    {
      yStart = Y_MIN;
      yEnd = Y_MAX;
      yStep = STEP_Y;
    }
    else
    {
      yStart = Y_MAX;
      yEnd = Y_MIN;
      yStep = -STEP_Y;
    }

    for (int dacY = yStart; (yStep > 0) ? (dacY <= yEnd) : (dacY >= yEnd); dacY += yStep)
    {
      // Compute DAC positions
      int dacYw = dacY + Y_OFFSET; // clamp(dacY + Y_OFFSET + simYOffset, 0, 4095);


      spi_transaction_t t2 = {};
      t2.length = 16;
      t2.flags = SPI_TRANS_USE_TXDATA;
      t2.tx_data[0] = (0b10110000 | ((dacYw >> 8) & 0x0F));
      t2.tx_data[1] = (dacYw & 0xFF);

      // Update DACs (atomic latch)
      GPIO.out_w1tc = (1U << PIN_NUM_LDAC);
      spi_device_polling_transmit(spi, &t2);
      GPIO.out_w1ts = (1U << PIN_NUM_LDAC);

      // add a little delay to allow settling
      esp_rom_delay_us(tPixelDwelltime);

      // --------------------------------------------------------------
      // PIXEL PULSE — synchronous, stable, and jitter-free
      // --------------------------------------------------------------
      if (ENABLE_TRIG_PIXEL)
      {
        // combine bits that should toggle together
        uint32_t mask_on = PIXEL_BIT; // only pixel goes high per dwell
        uint32_t mask_off = PIXEL_BIT;

        // atomic HIGH + dwell + atomic LOW
        GPIO.out_w1ts = mask_on;
        esp_rom_delay_us(tPixelDwelltime);
        GPIO.out_w1tc = mask_off;
      }

    }
}
    lineNumber++;
  }

  // frame complete
  currentFrame++;
  if (currentFrame >= nFrames)
    currentFrame = 0;
}

void SPIRenderer::drawFrame_()
{
  printf("Drawing frame %d\n", currentFrame + 1);

  // Set frame trigger if enabled
  if (ENABLE_TRIG_FRAME)
  {
    GPIO.out_w1ts = (1U << PIN_NUM_TRIG_FRAME);
  }
  if (ENABLE_TRIG_LINE)
  {
    GPIO.out_w1ts = (1U << PIN_NUM_TRIG_LINE);
  }
  if (ENABLE_TRIG_PIXEL)
  {
    GPIO.out_w1ts = (1U << PIN_NUM_TRIG_PIXEL);
  }

  // add a small delay to ensure the frame start is registered
  ets_delay_us(1);

  // Calculate SIM mode Y offset for this frame
  // In SIM mode, shift pattern vertically by STEP_Y/nFrames per frame
  int simYOffset = 0;
  if (SIM && nFrames > 0)
  {
    // Calculate fractional offset: (STEP_Y / nFrames) * currentFrame
    simYOffset = (STEP_Y * currentFrame) / nFrames;
  }

  // Track line number for snake pattern
  int lineNumber = 0;
  printf("Running FRAME with parameters: X[%d,%d] Y[%d,%d] OFF[%d,%d] STEP[%d,%d] dwell:%d frames:%d SNAKE:%d SIM:%d TRIG[F:%d L:%d P:%d]\n",
         X_MIN, X_MAX, Y_MIN, Y_MAX, X_OFFSET, Y_OFFSET, STEP_X, STEP_Y, tPixelDwelltime, nFrames, SNAKE, SIM,
         ENABLE_TRIG_FRAME, ENABLE_TRIG_LINE, ENABLE_TRIG_PIXEL);
  // Loop over X
  for (int dacX = X_MIN; dacX <= X_MAX; dacX += STEP_X)
  {
    // Determine Y scanning direction based on SNAKE or SIM mode
    int yStart, yEnd, yStep;
    if ((SNAKE || SIM) && (lineNumber % 2 == 1))
    {
      // Odd lines: scan from Y_MAX to Y_MIN (reverse)
      yStart = Y_MAX;
      yEnd = Y_MIN;
      yStep = -STEP_Y;
    }
    else
    {
      // Even lines (or non-snake/SIM mode): scan from Y_MIN to Y_MAX (forward)
      yStart = Y_MIN;
      yEnd = Y_MAX;
      yStep = STEP_Y;
    }

    // Loop over Y with direction determined above
    for (int dacY = yStart;
         (yStep > 0) ? (dacY <= yEnd) : (dacY >= yEnd);
         dacY += yStep)
    {
      // Clear triggers if enabled
      if (ENABLE_TRIG_PIXEL)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_PIXEL);
      }
      if (ENABLE_TRIG_LINE)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_LINE);
      }
      if (ENABLE_TRIG_FRAME)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_FRAME);
      }

      // Apply offsets to DAC values
      int dacXWithOffset = dacX + X_OFFSET;
      int dacYWithOffset = dacY + Y_OFFSET + simYOffset; // Add SIM offset

      // Clamp to valid DAC range (0-4095 for 12-bit DAC)
      dacXWithOffset = (dacXWithOffset < 0) ? 0 : ((dacXWithOffset > 4095) ? 4095 : dacXWithOffset);
      dacYWithOffset = (dacYWithOffset < 0) ? 0 : ((dacYWithOffset > 4095) ? 4095 : dacYWithOffset);
      // move to first pixel prior to triggering and pause for a bit, but not in snake/sim mode

      // Prepare SPI transactions for X and Y
      spi_transaction_t t1 = {};
      t1.length = 16;
      t1.flags = SPI_TRANS_USE_TXDATA;
      t1.tx_data[0] = (0b00110000 | ((dacXWithOffset >> 8) & 0x0F));
      t1.tx_data[1] = (dacXWithOffset & 0xFF);

      spi_transaction_t t2 = {};
      t2.length = 16;
      t2.flags = SPI_TRANS_USE_TXDATA;
      t2.tx_data[0] = (0b10110000 | ((dacYWithOffset >> 8) & 0x0F));
      t2.tx_data[1] = (dacYWithOffset & 0xFF);

      // Fewer LDAC toggles: latch once per pixel
      GPIO.out_w1tc = (1U << PIN_NUM_LDAC);  // hold LDAC low
      spi_device_polling_transmit(spi, &t1); // send X
      spi_device_polling_transmit(spi, &t2); // send Y
      GPIO.out_w1ts = (1U << PIN_NUM_LDAC);  // latch both channels

      // Optionally set a trigger directly for the pixel
      if (ENABLE_TRIG_PIXEL)
      {
        GPIO.out_w1ts = (1U << PIN_NUM_TRIG_PIXEL);
      }
      // Delay if needed:
      ets_delay_us(tPixelDwelltime);

      // Clear pixel trigger again
      /*
      if (ENABLE_TRIG_PIXEL) {
          GPIO.out_w1tc = (1U << PIN_NUM_TRIG_PIXEL);
      }
          */
    }

    // Optionally set line trigger here
    if (ENABLE_TRIG_LINE)
    {
      GPIO.out_w1ts = (1U << PIN_NUM_TRIG_LINE);
    }
    // Possibly delay
    ets_delay_us(5);

    /*
    // Clear line trigger
    if (ENABLE_TRIG_LINE)
    {
      GPIO.out_w1tc = (1U << PIN_NUM_TRIG_LINE);
    }


    // Possibly delay
    ets_delay_us(5);
*/
    // move scanner back to origin Y position in a sawtooth to be at the correct pixel location in the next line prior to triggering
    // Loop over Y with direction determined above
    for (int dacY = yEnd;
         dacY >= yStart;
         dacY -= yStep)
    {
      if (dacY == yEnd)
        printf("Returning to Y=%d\n", dacY);
      // Clear triggers if enabled
      if (ENABLE_TRIG_PIXEL)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_PIXEL);
      }
      if (ENABLE_TRIG_LINE)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_LINE);
      }
      if (ENABLE_TRIG_FRAME)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_FRAME);
      }

      // Apply offsets to DAC values
      int dacXWithOffset = dacX + X_OFFSET;
      int dacYWithOffset = dacY + Y_OFFSET + simYOffset; // Add SIM offset

      // Clamp to valid DAC range (0-4095 for 12-bit DAC)
      dacXWithOffset = (dacXWithOffset < 0) ? 0 : ((dacXWithOffset > 4095) ? 4095 : dacXWithOffset);
      dacYWithOffset = (dacYWithOffset < 0) ? 0 : ((dacYWithOffset > 4095) ? 4095 : dacYWithOffset);
      // move to first pixel prior to triggering and pause for a bit, but not in snake/sim mode

      spi_transaction_t t2 = {};
      t2.length = 16;
      t2.flags = SPI_TRANS_USE_TXDATA;
      t2.tx_data[0] = (0b10110000 | ((dacYWithOffset >> 8) & 0x0F));
      t2.tx_data[1] = (dacYWithOffset & 0xFF);

      // Fewer LDAC toggles: latch once per pixel
      GPIO.out_w1tc = (1U << PIN_NUM_LDAC); // hold LDAC low

      spi_device_polling_transmit(spi, &t2); // send Y
      GPIO.out_w1ts = (1U << PIN_NUM_LDAC);  // latch both channels

      // Optionally set a trigger directly for the pixel
      if (ENABLE_TRIG_PIXEL)
      {
        GPIO.out_w1ts = (1U << PIN_NUM_TRIG_PIXEL);
      }
      // Delay if needed:
      ets_delay_us(tPixelDwelltime);

      // Clear pixel trigger again

      if (ENABLE_TRIG_PIXEL)
      {
        GPIO.out_w1tc = (1U << PIN_NUM_TRIG_PIXEL);
      }
    }

    // Increment line number for snake pattern tracking
    lineNumber++;
  }
  // End of frame: clear triggers
  if (ENABLE_TRIG_PIXEL)
  {
    GPIO.out_w1tc = (1U << PIN_NUM_TRIG_PIXEL);
  }
  if (ENABLE_TRIG_LINE)
  {
    GPIO.out_w1tc = (1U << PIN_NUM_TRIG_LINE);
  }
  if (ENABLE_TRIG_FRAME)
  {
    GPIO.out_w1tc = (1U << PIN_NUM_TRIG_FRAME);
  }

  // Increment frame counter
  currentFrame++;
  if (currentFrame >= nFrames)
  {
    currentFrame = 0; // Loop back to frame 0
  }
}

////////////////////////////////////////////////////////////////
// Start rendering - renders one frame
////////////////////////////////////////////////////////////////
void SPIRenderer::start()
{
  drawFrame();
}

////////////////////////////////////////////////////////////////
// Set galvos to a stationary position (SINGLE mode)
////////////////////////////////////////////////////////////////
void SPIRenderer::setSinglePosition(int xpos, int ypos)
{
  // Clamp to valid DAC range (0-4095 for 12-bit DAC)
  xpos = (xpos < 0) ? 0 : ((xpos > 4095) ? 4095 : xpos);
  ypos = (ypos < 0) ? 0 : ((ypos > 4095) ? 4095 : ypos);

  // Prepare SPI transactions for X and Y
  spi_transaction_t t1 = {};
  t1.length = 16;
  t1.flags = SPI_TRANS_USE_TXDATA;
  t1.tx_data[0] = (0b00110000 | ((xpos >> 8) & 0x0F));
  t1.tx_data[1] = (xpos & 0xFF);

  spi_transaction_t t2 = {};
  t2.length = 16;
  t2.flags = SPI_TRANS_USE_TXDATA;
  t2.tx_data[0] = (0b10110000 | ((ypos >> 8) & 0x0F));
  t2.tx_data[1] = (ypos & 0xFF);

  // Set LDAC low
  GPIO.out_w1tc = (1U << PIN_NUM_LDAC);

  // Send X and Y values
  spi_device_polling_transmit(spi, &t1); // Send X value
  spi_device_polling_transmit(spi, &t2); // Send Y value

  // Latch both channels
  gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 1);

  printf("Set single position: X=%d, Y=%d\n", xpos, ypos);
}

/*
void SPIRenderer::draw()
{
  // Outer loop: frames
  for (int iFrame = 0; iFrame < nFrames; iFrame++)
  {
    printf("Drawing frame %d of %d\n", iFrame + 1, nFrames);
    printf("X_MIN %d, X_MAX %d, Y_MIN %d, Y_MAX %d, STEP %d\n",
           X_MIN, X_MAX, Y_MIN, Y_MAX, STEP);

    // Example: set all triggers high at the start of a frame
    // pixelTrigVal, lineTrigVal, frameTrigVal
    set_gpio_pins(1, 1, 1);

    // Loop over X
    for (int dacX = X_MIN; dacX <= X_MAX; dacX += STEP)
    {

      // Loop over Y
      for (int dacY = Y_MIN; dacY <= Y_MAX; dacY += STEP)
      {
        set_gpio_pins(0, 0, 0);

        //ESP_LOGI(TAG, "Drawing pixel at %d %d", dacX, dacY);

        // SPI transaction for channel A (X-axis)
        spi_transaction_t t1 = {};
        t1.length = 16; // 16 bits
        t1.flags = SPI_TRANS_USE_TXDATA;
        t1.tx_data[0] = (0b00110000 | ((dacX >> 8) & 0x0F)); // Bit 5 = 1 (Gain = 1)
        t1.tx_data[1] = (dacX & 0xFF);

        // SPI transaction for channel B (Y-axis)
        spi_transaction_t t2 = {};
        t2.length = 16;
        t2.flags = SPI_TRANS_USE_TXDATA;
        t2.tx_data[0] = (0b10110000 | ((dacY >> 8) & 0x0F)); // Bit 5 = 1 (Gain = 1)
        t2.tx_data[1] = (dacY & 0xFF);

        // Latch the DAC
        gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 0);
        gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 1);

        gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 0); // Hold LDAC low
        spi_device_polling_transmit(spi, &t1);       // Send X value
        spi_device_polling_transmit(spi, &t2);       // Send Y value
        gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 1); // Latch both channels

        // Trigger the camera for each pixel
        // trigger_camera(this->tPixelDwelltime, PIN_NUM_TRIG_PIXEL);

        // Possibly clear certain triggers
        set_gpio_pins(1, 0, 0);
      }
      // Possibly clear certain triggers
      set_gpio_pins(1, 1, 0);
    }
    // End of frame
    set_gpio_pins(0, 0, 0);
  }
}
  */

////////////////////////////////////////////////////////////////
// Point cloud methods
////////////////////////////////////////////////////////////////

void SPIRenderer::clearPointCloud()
{
  pointCloudX.clear();
  pointCloudY.clear();
  pointCloudIndex = 0;
  ESP_LOGI(TAG, "Point cloud cleared");
}

void SPIRenderer::addPoint(uint16_t x, uint16_t y)
{
  // Clamp to valid DAC range
  if (x > 4095) x = 4095;
  if (y > 4095) y = 4095;

  pointCloudX.push_back(x);
  pointCloudY.push_back(y);
  ESP_LOGD(TAG, "Added point: (%d, %d), total points: %d", x, y, pointCloudX.size());
}

void SPIRenderer::setPointCloud(const std::vector<uint16_t>& xCoords, const std::vector<uint16_t>& yCoords)
{
  if (xCoords.size() != yCoords.size()) {
    ESP_LOGE(TAG, "Point cloud X and Y coordinate arrays must be same size");
    return;
  }

  pointCloudX = xCoords;
  pointCloudY = yCoords;
  pointCloudIndex = 0;

  ESP_LOGI(TAG, "Point cloud set with %d points", pointCloudX.size());
}

void SPIRenderer::renderPointCloud()
{
  if (pointCloudX.empty()) {
    ESP_LOGW(TAG, "Point cloud is empty, nothing to render");
    return;
  }

  ESP_LOGI(TAG, "Rendering point cloud with %d points", pointCloudX.size());

  // Frame trigger
  if (ENABLE_TRIG_FRAME) {
    gpio_set_level((gpio_num_t)PIN_NUM_TRIG_FRAME, 1);
  }

  // Iterate through all points in the cloud
  for (size_t i = 0; i < pointCloudX.size(); i++) {
    uint16_t dacX = pointCloudX[i];
    uint16_t dacY = pointCloudY[i];

    // Apply offsets and clamp
    int dacXWithOffset = dacX + X_OFFSET;
    int dacYWithOffset = dacY + Y_OFFSET;
    dacXWithOffset = (dacXWithOffset < 0) ? 0 : ((dacXWithOffset > 4095) ? 4095 : dacXWithOffset);
    dacYWithOffset = (dacYWithOffset < 0) ? 0 : ((dacYWithOffset > 4095) ? 4095 : dacYWithOffset);

    // Prepare SPI transactions for X and Y
    spi_transaction_t t1 = {};
    t1.length = 16;
    t1.flags = SPI_TRANS_USE_TXDATA;
    t1.tx_data[0] = (0b00110000 | ((dacXWithOffset >> 8) & 0x0F));
    t1.tx_data[1] = (dacXWithOffset & 0xFF);

    spi_transaction_t t2 = {};
    t2.length = 16;
    t2.flags = SPI_TRANS_USE_TXDATA;
    t2.tx_data[0] = (0b10110000 | ((dacYWithOffset >> 8) & 0x0F));
    t2.tx_data[1] = (dacYWithOffset & 0xFF);

    // Send SPI data and latch
    gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 0);
    spi_device_polling_transmit(spi, &t1);
    spi_device_polling_transmit(spi, &t2);
    gpio_set_level((gpio_num_t)PIN_NUM_LDAC, 1);

    // Pixel trigger
    if (ENABLE_TRIG_PIXEL) {
      gpio_set_level((gpio_num_t)PIN_NUM_TRIG_PIXEL, 1);
      if (tPixelDwelltime > 0) {
        esp_rom_delay_us(tPixelDwelltime);
      }
      gpio_set_level((gpio_num_t)PIN_NUM_TRIG_PIXEL, 0);
    } else if (tPixelDwelltime > 0) {
      esp_rom_delay_us(tPixelDwelltime);
    }
  }

  // Clear frame trigger
  if (ENABLE_TRIG_FRAME) {
    gpio_set_level((gpio_num_t)PIN_NUM_TRIG_FRAME, 0);
  }

  ESP_LOGI(TAG, "Point cloud rendering complete");
}
