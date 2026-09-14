#define IS_XIAO 1
#include <esp_attr.h>
#include <vector>

// LSFT note: the two branches permute SCK/SDI/CS across the same D8/D9/D10
// pads. Both were tried on this rig and both stayed silent, so the pin map
// is probably not the fault -- back on the vendor default for this board.
// If the galvo still does not move once the analog rails are confirmed,
// comment this out to fall through to the plain IS_XIAO mapping.
#define IS_XIAO_UC2GALVOBOARD 1
#ifdef IS_XIAO_UC2GALVOBOARD
#define PIN_NUM_MISO -1
/*
constexpr int PIN_DAC_SDI      = D10; // GPIO9  SPI SDI (MOSI)
*/
#define PIN_NUM_SCK GPIO_NUM_7        // D9
#define PIN_NUM_SDI GPIO_NUM_9          // D8
#define PIN_NUM_CS GPIO_NUM_8        // D10
#define PIN_NUM_LDAC GPIO_NUM_6      // D7
#define PIN_NUM_LASER GPIO_NUM_43     //
#define PIN_NUM_TRIG_PIXEL GPIO_NUM_2 // D1
#define PIN_NUM_TRIG_LINE GPIO_NUM_3  // D2
#define PIN_NUM_TRIG_FRAME GPIO_NUM_4 // D3
#elif IS_XIAO
/*
D0: 1
D1: 2
D2: 3
D3: 4
D4: 5
D5: 6
D6: 43
D7: 44
D8: 7
D9: 8
D10: 9
*/
#define PIN_NUM_MISO -1
#define PIN_NUM_SCK GPIO_NUM_8        // D9
#define PIN_NUM_SDI GPIO_NUM_7        // D8
#define PIN_NUM_CS GPIO_NUM_9         // D10
#define PIN_NUM_LDAC GPIO_NUM_6      // D7
#define PIN_NUM_LASER GPIO_NUM_43     //
#define PIN_NUM_TRIG_PIXEL GPIO_NUM_2 // D1
#define PIN_NUM_TRIG_LINE GPIO_NUM_3  // D2
#define PIN_NUM_TRIG_FRAME GPIO_NUM_4 // D3

#else
static const char *TAG = "Renderer";
#define PIN_NUM_MISO -1
#define PIN_NUM_SCK GPIO_NUM_14
#define PIN_NUM_SDI GPIO_NUM_12   // 26
#define PIN_NUM_CS GPIO_NUM_13    // 27
#define PIN_NUM_LDAC GPIO_NUM_27  // GPIO_NUM_33
#define PIN_NUM_LASER GPIO_NUM_26 //
#define PIN_NUM_TRIG_PIXEL GPIO_NUM_23
#define PIN_NUM_TRIG_LINE GPIO_NUM_22x
#define PIN_NUM_TRIG_FRAME GPIO_NUM_21

#endif


typedef struct spi_device_t *spi_device_handle_t; ///< Handle for a device on a SPI bus

class SPIRenderer
{
private:
  spi_device_handle_t spi;
  void  drawFrame();
  void  drawFrame_();
  int nX;
  int nY;
  int tPixelDwelltime;
  int X_MIN = 0;
  int X_MAX = 2048;
  int Y_MIN = 0;
  int Y_MAX = 2048;
  int X_OFFSET = 0;
  int Y_OFFSET = 0;
  int STEP_X = 64;
  int STEP_Y = 64;
  int nFrames = 1;
  int currentFrame = 0;  // Track current frame for continuous operation
  bool SNAKE = false; // Snake scanning pattern
  bool SIM = false;   // Structured illumination mode
  bool ENABLE_TRIG_FRAME = true;
  bool ENABLE_TRIG_LINE = true;
  bool ENABLE_TRIG_PIXEL = true;
  
  // Point cloud mode
  std::vector<uint16_t> pointCloudX;
  std::vector<uint16_t> pointCloudY;
  int pointCloudIndex = 0;

public:
  SPIRenderer(int xmin, int xmax, int ymin, int ymax, int xoffset, int yoffset, 
              int stepx, int stepy, int tPixelDwelltime, int nFramesI, bool snake = false, bool sim = false,
              bool enableTrigFrame = true, bool enableTrigLine = true, bool enableTrigPixel = true);
  void start();  // Renders one frame and returns
  void setParameters(int xmin, int xmax, int ymin, int ymax, int xoffset, int yoffset,
                     int stepx, int stepy, int tPixelDwelltime, int nFramesI, bool snake = false, bool sim = false,
                     bool enableTrigFrame = true, bool enableTrigLine = true, bool enableTrigPixel = true);
  void setSinglePosition(int xpos, int ypos);  // Set galvos to a stationary position
  
  // Point cloud methods
  void clearPointCloud();
  void addPoint(uint16_t x, uint16_t y);
  void setPointCloud(const std::vector<uint16_t>& xCoords, const std::vector<uint16_t>& yCoords);
  void renderPointCloud();  // Render all points in the cloud

};
