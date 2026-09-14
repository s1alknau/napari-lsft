# Implementation Notes - Serial JSON Interface

## Requirements Met

### 1. JSON-based Serial Interface ✓
Implemented JSON command parsing using ArduinoJson library (v6.21.2), matching the UC2-ESP firmware standard.

### 2. Command Structure ✓
Following the exact format specified:
```json
{"task":"/galvo_act", "qid":1, "X_MIN":0, "X_MAX":2048, "Y_MIN":0, "Y_MAX":2048, "STEP_X":100, "STEP_Y":100, "tPixelDwelltime":0, "nFrames":10, "SNAKE":true}
```

### 3. Parameters Adjustable ✓
All requested parameters are configurable via serial:
- X_MIN (int): Minimum X coordinate
- X_MAX (int): Maximum X coordinate  
- Y_MIN (int): Minimum Y coordinate
- Y_MAX (int): Maximum Y coordinate
- STEP (int): Step size/resolution
- tPixelDwelltime (int): Pixel dwell time in microseconds
- nFrames (int): Number of frames to render
- SNAKE (bool): Snake scanning pattern - alternates line direction for optimized scanning

### 4. Similar Architecture to Reference ✓
Implementation follows the waveshare LED array pattern:
- Uses ArduinoJson for parsing
- Handles `/state_get` command for device identification
- Handles `/galvo_act` command for parameter updates
- **NEW**: Handles `/galvo_get` command for parameter retrieval
- **NEW**: Persistent storage using ESP32 Preferences library
- Returns status messages with `++` and `--` delimiters
- Echoes `qid` in responses for request tracking
- Processes serial input line-by-line

### 5. Response Format ✓
Responses follow UC2 standard:
```json
++
{"task":"/galvo_act","status":"success","qid":1}
--
```

### 6. Persistent Storage ✓
Parameters are automatically saved to non-volatile storage (NVS) and restored on boot:
- Uses ESP32 Preferences library
- Namespace: "galvo"
- Saved on every `/galvo_act` command
- Loaded automatically during `app_main()` initialization
- Default values used if no saved preferences exist

## Key Implementation Details

### Architecture
- **Non-blocking**: Serial processing happens in main loop without blocking rendering
- **Default values**: All parameters have sensible defaults that are retained if not specified
- **Immediate effect**: Parameter changes take effect on the next renderer cycle
- **Error handling**: Validates JSON syntax and command structure

### Serial Configuration
- Baud rate: 115200
- Line ending: Newline (\n)
- Format: JSON with single line per command

### Code Organization
```
src/main.cpp:
├── Global variables (parameters, renderer, serial buffer)
├── Preferences object for persistent storage
├── saveParameters() - Save parameters to NVS
├── loadParameters() - Load parameters from NVS
├── handleJSON() - Parse and dispatch commands
│   ├── /state_get handler
│   ├── /galvo_act handler (saves to preferences)
│   └── /galvo_get handler (returns current parameters)
├── processSerial() - Read and buffer serial input
└── app_main() - Initialize, load preferences, and main loop

src/SPIRenderer.cpp:
└── draw() - Rendering loop with snake pattern support
    ├── Normal scanning: All lines scan Y_MIN → Y_MAX
    └── Snake scanning: Alternates direction (even: →, odd: ←)
```

### Persistent Storage Implementation
Uses ESP32 Preferences library for non-volatile storage:

**Save Operation (on `/galvo_act`):**
```cpp
preferences.begin("galvo", false);  // read-write mode
preferences.putInt("X_MIN", X_MIN);
preferences.putInt("X_MAX", X_MAX);
// ... save all parameters
preferences.end();
```

**Load Operation (on boot):**
```cpp
preferences.begin("galvo", true);   // read-only mode
X_MIN = preferences.getInt("X_MIN", 0);  // default: 0
X_MAX = preferences.getInt("X_MAX", 6000);  // default: 6000
// ... load all parameters with defaults
preferences.end();
```

### Snake Scanning Pattern
The SNAKE parameter enables an optimized scanning pattern:

**Implementation:**
- Tracks line number during scanning
- Even lines (0, 2, 4...): Scan from Y_MIN to Y_MAX (forward)
- Odd lines (1, 3, 5...): Scan from Y_MAX to Y_MIN (backward)

**Benefits:**
- Eliminates flyback time between lines
- Reduces mechanical stress on galvo mirrors
- Improves scanning speed
- More continuous motion profile

**Code logic:**
```cpp
if (SNAKE && (lineNumber % 2 == 1)) {
    // Odd lines: scan backward
    yStart = Y_MAX; yEnd = Y_MIN; yStep = -STEP;
} else {
    // Even lines: scan forward
    yStart = Y_MIN; yEnd = Y_MAX; yStep = STEP;
}
```

## Compatibility Notes

### ESP32-S3 XIAO Support
The implementation works with both ESP32 dev board and ESP32-S3 XIAO configurations:
- Uses Arduino framework for Serial and String support
- Compatible with ESP-IDF framework used by SPIRenderer
- ArduinoJson added to both platformio.ini environments

### Differences from Reference
Minor adaptations made for galvo scanner vs LED array:
1. Device identifier: "UC2_GalvoScanner" instead of "UC2_Feather"
2. Command task: "/galvo_act" instead of "/ledarr_act"
3. Parameters: Galvo-specific (X/Y ranges, steps) vs LED-specific (colors, patterns)

## Testing

### Manual Testing
Use serial terminal or Python script to send commands:
```bash
# Using provided test script
python test_serial_interface.py /dev/ttyUSB0

# Using screen
screen /dev/ttyUSB0 115200
{"task":"/state_get","qid":1}
```

### Expected Behavior
1. Device continuously scans with current parameters
2. Serial commands are processed between scan cycles
3. Parameters update immediately when command received
4. No interruption to scanning operation

## Security

✓ CodeQL analysis passed with no vulnerabilities detected
✓ Input validation on JSON parsing
✓ Bounds checking on parameter values (via existing SPIRenderer logic)
✓ No buffer overflow risks with String class

## Future Enhancements (Optional)

Potential additions not in current scope:
- Parameter range validation before applying
- Ability to pause/resume scanning
- Query current parameter values
- Save/load parameter presets
- Multiple scanning patterns/modes
