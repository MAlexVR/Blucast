#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "nvCVOpenCV.h"
#include "nvVideoEffects.h"
#include "opencv2/opencv.hpp"
#include "opencv2/dnn.hpp"
#include "opencv2/objdetect.hpp"

//  Paths ───────────────────────────────────────────────────────────────
static const char *SHARED_DIR      = "/tmp/blucast";
static const char *CMD_PIPE_PATH   = "/tmp/blucast/cmd.pipe";
static const char *CONSUMERS_FILE  = "/tmp/blucast/consumers";
static const char *PREVIEW_FILE    = "/tmp/blucast/preview.jpg";
static const char *PREVIEW_TMP     = "/tmp/blucast/preview.jpg.tmp";
static const char *PID_FILE        = "/tmp/blucast/server.pid";
static const char *VCAM_DEVICE     = "/dev/video10";
static const char *FACE_CASCADE_PATH   = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_alt2.xml";
static const char *FACE_MODEL_PROTOTXT = "/app/models/face_detector.prototxt";
static const char *FACE_MODEL_WEIGHTS  = "/app/models/face_detector.caffemodel";

//  Global state
static std::atomic<bool>  g_running{true};
static std::atomic<bool>  g_windowVisible{true};
static std::atomic<int>   g_effectMode{6};
static std::atomic<float> g_blurStrength{0.5f};
static std::atomic<float> g_zoomFactor{1.0f};
static std::atomic<float> g_panX{0.0f};  // -1.0 (left)  .. 1.0 (right)
static std::atomic<float> g_panY{0.0f};  // -1.0 (up)    .. 1.0 (down)
static std::atomic<bool>  g_autoReframe{false};
static std::atomic<int>   g_reframeModel{1};  // 0=Haar cascade, 1=DNN SSD
static std::atomic<float> g_reframeMinZoom{1.15f};   // 1.0 .. 2.0
static std::atomic<float> g_reframeSmoothing{0.8f};  // 0.5 (fast) .. 0.95 (smooth)
static std::atomic<bool>  g_virtualLight{false};
static std::atomic<float> g_virtualLightIntensity{0.5f};  // 0.0 .. 1.0
static std::atomic<int>   g_cameraWidth{1280};
static std::atomic<int>   g_cameraHeight{720};
static std::atomic<int>   g_cameraFps{30};
static std::atomic<bool>  g_cameraSettingsChanged{false};

static std::mutex  g_deviceMutex;
static std::string g_inputDevice;
static bool        g_deviceChanged = false;

static std::mutex  g_bgMutex;
static std::string g_bgFile;
static bool        g_bgChanged = false;

//  Effect modes ────────────────────────────────────────────────────────
enum EffectMode {
    MODE_MATTE    = 0,
    MODE_LIGHT    = 1,
    MODE_GREEN    = 2,
    MODE_WHITE    = 3,
    MODE_NONE     = 4,
    MODE_BG       = 5,
    MODE_BLUR     = 6,
    MODE_DENOISE  = 7,
};

//  Utility: read consumer count from file ──────────────────────────────
static int readConsumerCount() {
    FILE *f = fopen(CONSUMERS_FILE, "r");
    if (!f) return 0;
    int n = 0;
    if (fscanf(f, "%d", &n) != 1) n = 0;
    fclose(f);
    return n < 0 ? 0 : n;
}

//  Utility: write PID file ─────────────────────────────────────────────
static void writePidFile() {
    FILE *f = fopen(PID_FILE, "w");
    if (f) {
        fprintf(f, "%d\n", getpid());
        fclose(f);
    }
}

//  Digital zoom: crop a region and scale it back up to the full frame size,
//  applied to the raw camera frame *before* any effect runs, so green
//  screen/blur/etc. all operate on the zoomed-in, recentered view of the
//  person. panX/panY (-1..1) shift the crop window within the room left by
//  zooming, so the framing can be recentered instead of always cropping
//  symmetrically from the middle.
static void applyZoom(cv::Mat &frame, float zoom, float panX, float panY) {
    if (zoom <= 1.0f || frame.empty()) return;
    int cropW = std::max(1, (int)std::round(frame.cols / zoom));
    int cropH = std::max(1, (int)std::round(frame.rows / zoom));
    int maxShiftX = (frame.cols - cropW) / 2;
    int maxShiftY = (frame.rows - cropH) / 2;
    int x = maxShiftX + (int)std::round(panX * maxShiftX);
    int y = maxShiftY + (int)std::round(panY * maxShiftY);
    x = std::clamp(x, 0, frame.cols - cropW);
    y = std::clamp(y, 0, frame.rows - cropH);
    cv::Mat cropped = frame(cv::Rect(x, y, cropW, cropH));
    cv::Mat zoomed;
    cv::resize(cropped, zoomed, frame.size(), 0, 0, cv::INTER_LINEAR);
    frame = zoomed;
}

// ══════════════════════════════════════════════════════════════════════════
// Auto Reframe: detects the user's face and drives the same zoom/pan used
// by the manual controls to keep the face centered and a comfortable size,
// smoothed over time so the framing doesn't jitter frame to frame.
//
// Two selectable detector backends, both CPU-only with no NVIDIA SDK
// dependency:
//   - Haar cascade: lighter, but noticeably misses non-frontal head angles.
//   - DNN SSD (res10_300x300): heavier, far more reliable across angle and
//     partial occlusion (glasses, headphones).
// ══════════════════════════════════════════════════════════════════════════
enum class ReframeModel { Haar = 0, Dnn = 1 };

// Shared by AutoReframer and VirtualLight: runs the res10_300x300 SSD face
// detector and returns the highest-confidence face box, if any clears the
// threshold.
static bool detectFaceDnn(cv::dnn::Net &net, const cv::Mat &frame, cv::Rect2f &outFace,
                           float confidenceThreshold = 0.5f) {
    cv::Mat blob = cv::dnn::blobFromImage(frame, 1.0, cv::Size(300, 300),
        cv::Scalar(104.0, 177.0, 123.0), false, false);
    net.setInput(blob);
    cv::Mat detections = net.forward();
    cv::Mat results(detections.size[2], detections.size[3], CV_32F, detections.ptr<float>());

    float bestConfidence = confidenceThreshold;
    bool found = false;
    for (int i = 0; i < results.rows; i++) {
        float confidence = results.at<float>(i, 2);
        if (confidence <= bestConfidence) continue;
        float x1 = results.at<float>(i, 3) * frame.cols;
        float y1 = results.at<float>(i, 4) * frame.rows;
        float x2 = results.at<float>(i, 5) * frame.cols;
        float y2 = results.at<float>(i, 6) * frame.rows;
        bestConfidence = confidence;
        outFace = cv::Rect2f(x1, y1, x2 - x1, y2 - y1);
        found = true;
    }
    return found;
}

class AutoReframer {
public:
    bool initHaar(const std::string &cascadePath) {
        haarLoaded_ = cascade_.load(cascadePath);
        if (!haarLoaded_) std::cerr << "AutoReframe: failed to load cascade: " << cascadePath << std::endl;
        return haarLoaded_;
    }

    bool initDnn(const std::string &prototxtPath, const std::string &weightsPath) {
        try {
            net_ = cv::dnn::readNetFromCaffe(prototxtPath, weightsPath);
        } catch (const cv::Exception &e) {
            std::cerr << "AutoReframe: failed to load DNN face detector: " << e.what() << std::endl;
            return false;
        }
        dnnLoaded_ = !net_.empty();
        return dnnLoaded_;
    }

    void setModel(ReframeModel m) { model_ = m; }
    void setMinZoom(float z) { minZoom_ = z; }
    void setSmoothing(float s) { smoothing_ = s; }

    // Computes a smoothed (zoom, panX, panY) that keeps the detected face
    // centered. Returns false if no face has ever been locked onto yet, in
    // which case the caller should fall back to the manual zoom/pan values.
    bool update(const cv::Mat &frame, float &outZoom, float &outPanX, float &outPanY) {
        if (frame.empty()) return false;

        // Detection is the expensive part; re-running it every single frame
        // is wasted work; wasted work compounds badly once another feature
        // (Virtual Light) also runs its own detector each frame. The
        // existing smoothing already holds the last known position between
        // detections, so skipping most frames is visually seamless.
        cv::Rect2f bestFace;
        bool found = false;
        if (++frameCounter_ % kDetectEveryN == 0) {
            found = (model_ == ReframeModel::Dnn && dnnLoaded_) ? detectDnn(frame, bestFace)
                  : (model_ == ReframeModel::Haar && haarLoaded_) ? detectHaar(frame, bestFace)
                  : false;
        }

        if (found) {
            float faceCenterX = bestFace.x + bestFace.width / 2.0f;
            float faceCenterY = bestFace.y + bestFace.height / 2.0f;
            float faceH = bestFace.height;

            // A floor above 1.0x guarantees some crop margin even when the face
            // already fills the frame (e.g. sitting close to a laptop webcam) —
            // without it, zoom clamps to 1.0x and panning has no room to work,
            // so the feature would do nothing for anyone already well-framed.
            float targetZoom = std::clamp(std::max(minZoom_,
                (kTargetFaceHeightFrac * frame.rows) / std::max(faceH, 1.0f)), 1.0f, 2.0f);
            float cropW = frame.cols / targetZoom;
            float cropH = frame.rows / targetZoom;
            float maxShiftX = (frame.cols - cropW) / 2.0f;
            float maxShiftY = (frame.rows - cropH) / 2.0f;
            float targetPanX = maxShiftX > 0.5f
                ? std::clamp((faceCenterX - cropW / 2.0f - maxShiftX) / maxShiftX, -1.0f, 1.0f) : 0.0f;
            float targetPanY = maxShiftY > 0.5f
                ? std::clamp((faceCenterY - cropH / 2.0f - maxShiftY) / maxShiftY, -1.0f, 1.0f) : 0.0f;

            hasLock_ = true;
            smoothZoom_ = smoothZoom_ * smoothing_ + targetZoom * (1.0f - smoothing_);
            smoothPanX_ = smoothPanX_ * smoothing_ + targetPanX * (1.0f - smoothing_);
            smoothPanY_ = smoothPanY_ * smoothing_ + targetPanY * (1.0f - smoothing_);
        }
        // If no face this frame, hold the last smoothed framing instead of
        // snapping back to 1x — avoids jarring jumps on brief detection misses.
        if (!hasLock_) return false;
        outZoom = smoothZoom_;
        outPanX = smoothPanX_;
        outPanY = smoothPanY_;
        return true;
    }

private:
    bool detectDnn(const cv::Mat &frame, cv::Rect2f &outFace) {
        return detectFaceDnn(net_, frame, outFace, kConfidenceThreshold);
    }

    bool detectHaar(const cv::Mat &frame, cv::Rect2f &outFace) {
        cv::Mat gray;
        cv::cvtColor(frame, gray, cv::COLOR_BGR2GRAY);
        cv::equalizeHist(gray, gray);
        std::vector<cv::Rect> faces;
        cascade_.detectMultiScale(gray, faces, 1.1, 3, 0, cv::Size(60, 60));
        if (faces.empty()) return false;
        outFace = *std::max_element(faces.begin(), faces.end(),
            [](const cv::Rect &a, const cv::Rect &b) { return a.area() < b.area(); });
        return true;
    }

    static constexpr float kTargetFaceHeightFrac = 0.35f; // keep face ~35% of frame height
    static constexpr float kConfidenceThreshold = 0.5f;

    cv::CascadeClassifier cascade_;
    cv::dnn::Net net_;
    bool haarLoaded_ = false, dnnLoaded_ = false;
    ReframeModel model_ = ReframeModel::Dnn;
    float minZoom_ = 1.15f;   // floor so pan always has crop margin; user-configurable
    float smoothing_ = 0.8f;  // higher = smoother/slower to react; user-configurable
    bool hasLock_ = false;
    float smoothZoom_ = 1.0f, smoothPanX_ = 0.0f, smoothPanY_ = 0.0f;
    int frameCounter_ = 0;
    static constexpr int kDetectEveryN = 2; // run the detector every other frame
};

static AutoReframer g_reframer;

// ══════════════════════════════════════════════════════════════════════════
// Virtual Light: approximates NVIDIA Broadcast's "Virtual Key Light" (an AI
// relighting model that doesn't run on this GPU — same TensorRT/certified-
// chip wall as Eye Contact and native Auto Reframe). Instead of a real
// relighting model, this brightens a soft, face-anchored region — like an
// actual light aimed at the user — using the same DNN face detector as
// Auto Reframe, independent of whether Auto Reframe is enabled.
// ══════════════════════════════════════════════════════════════════════════
class VirtualLight {
public:
    bool init(const std::string &prototxtPath, const std::string &weightsPath) {
        try {
            net_ = cv::dnn::readNetFromCaffe(prototxtPath, weightsPath);
        } catch (const cv::Exception &e) {
            std::cerr << "VirtualLight: failed to load face detector: " << e.what() << std::endl;
            return false;
        }
        loaded_ = !net_.empty();
        return loaded_;
    }

    // personMatte, when non-null and non-empty, is the background-removal
    // segmentation mask (0..255) for this exact frame — the light is ANDed
    // against it so it can never bleed onto a replaced/blurred background,
    // only the actual person. Call this after compositing (on the frame the
    // viewer will actually see), not on the raw pre-effect frame: the alpha
    // mask makes the result identical to relighting the person alone, while
    // guaranteeing zero leakage into whatever background ends up behind them.
    void apply(cv::Mat &frame, float intensity, const cv::Mat *personMatte = nullptr) {
        if (!loaded_ || frame.empty() || intensity <= 0.0f) return;

        // Same reasoning as AutoReframer: skip most frames' detection and
        // hold the last known face position — keeps this affordable even
        // when Auto Reframe's own detector is also running every frame.
        cv::Rect2f face;
        if (++frameCounter_ % kDetectEveryN == 0 && detectFaceDnn(net_, frame, face)) {
            if (!hasLock_) {
                lastFace_ = face;
            } else {
                float a = 1.0f - kSmoothing;
                lastFace_.x += (face.x - lastFace_.x) * a;
                lastFace_.y += (face.y - lastFace_.y) * a;
                lastFace_.width += (face.width - lastFace_.width) * a;
                lastFace_.height += (face.height - lastFace_.height) * a;
            }
            hasLock_ = true;
        }
        if (!hasLock_) return;

        // Soft ellipse over face + upper body, like a key light aimed at the
        // user — feathered heavily so there's no visible edge.
        cv::Point center((int)(lastFace_.x + lastFace_.width / 2.0f),
                          (int)(lastFace_.y + lastFace_.height * 0.65f));
        cv::Size axes((int)(lastFace_.width * 1.4f), (int)(lastFace_.height * 2.0f));
        if (axes.width <= 0 || axes.height <= 0) return;

        cv::Mat mask = cv::Mat::zeros(frame.size(), CV_8UC1);
        cv::ellipse(mask, center, axes, 0, 0, 360, cv::Scalar(255), -1);
        // GaussianBlur's cost scales with kernel size; using the raw ellipse
        // axes as the kernel (unbounded — hundreds of px for a large/zoomed
        // face) made this take ~3 SECONDS per frame instead of a few ms,
        // which is what actually looked like a freeze. A capped kernel still
        // gives a soft, wide feather without the runaway cost.
        int blurSize = std::min(81, std::max(axes.width, axes.height) | 1);
        cv::GaussianBlur(mask, mask, cv::Size(blurSize, blurSize), 0);

        if (personMatte != nullptr && !personMatte->empty() && personMatte->size() == frame.size()) {
            cv::bitwise_and(mask, *personMatte, mask);
        }

        // Adaptive shadow lift: measure current face brightness under the
        // mask and scale it toward a comfortable target, so already
        // well-lit faces aren't washed out — only actual shadow gets opened
        // up, the way a real fill light would behave.
        cv::Mat gray;
        cv::cvtColor(frame, gray, cv::COLOR_BGR2GRAY);
        double faceLum = cv::mean(gray, mask)[0];
        float ratio = (faceLum > 1.0)
            ? std::clamp((float)(kTargetLuminance / faceLum), 1.0f, kMaxRatio) : 1.0f;
        float adjRatio = 1.0f + (ratio - 1.0f) * intensity;

        cv::Mat maskF;
        mask.convertTo(maskF, CV_32F, 1.0 / 255.0);
        cv::Mat mask3f;
        cv::cvtColor(maskF, mask3f, cv::COLOR_GRAY2BGR);

        // Scale (not just add) brightness so the lift is proportional to how
        // dark the face actually is, with a slight warm tint like a real light.
        cv::Mat lit;
        frame.convertTo(lit, CV_32FC3, adjRatio, 4.0);
        std::vector<cv::Mat> ch;
        cv::split(lit, ch);
        ch[2] += 6.0f; // BGR: warm the red channel a bit
        cv::merge(ch, lit);

        cv::Mat frameF, outF;
        frame.convertTo(frameF, CV_32FC3);
        outF = lit.mul(mask3f) + frameF.mul(cv::Scalar(1, 1, 1) - mask3f);
        outF.convertTo(frame, CV_8UC3);
    }

private:
    static constexpr float kSmoothing = 0.8f;
    static constexpr double kTargetLuminance = 170.0; // comfortable brightness to lift shadows toward
    static constexpr float kMaxRatio = 1.5f;           // cap so it never blows out highlights

    cv::dnn::Net net_;
    bool loaded_ = false;
    bool hasLock_ = false;
    cv::Rect2f lastFace_;
    int frameCounter_ = 0;
    static constexpr int kDetectEveryN = 2; // run the detector every other frame
};

static VirtualLight g_virtualLightFx;

// ══════════════════════════════════════════════════════════════════════════
// Virtual Camera
// ══════════════════════════════════════════════════════════════════════════
class VirtualCamera {
public:
    VirtualCamera() : fd_(-1), width_(0), height_(0) {}

    ~VirtualCamera() {
        if (fd_ >= 0) {
            int type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
            ioctl(fd_, VIDIOC_STREAMOFF, &type);
            close(fd_);
        }
    }

    bool open(int width, int height, int fps) {
        // reopen if resolution changed
        if (fd_ >= 0 && (width_ != width || height_ != height)) {
            int type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
            ioctl(fd_, VIDIOC_STREAMOFF, &type);
            close(fd_);
            fd_ = -1;
        }
        if (fd_ >= 0) return true;

        fd_ = ::open(VCAM_DEVICE, O_WRONLY);
        if (fd_ < 0) {
            std::cerr << "Cannot open virtual camera " << VCAM_DEVICE << std::endl;
            return false;
        }

        struct v4l2_format fmt{};
        fmt.type                 = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        fmt.fmt.pix.width        = width;
        fmt.fmt.pix.height       = height;
        fmt.fmt.pix.pixelformat  = V4L2_PIX_FMT_YUV420;
        fmt.fmt.pix.sizeimage    = width * height * 3 / 2;
        fmt.fmt.pix.field        = V4L2_FIELD_NONE;
        if (ioctl(fd_, VIDIOC_S_FMT, &fmt) < 0) {
            std::cerr << "Warning: VIDIOC_S_FMT failed (device may be locked)" << std::endl;
        }

        struct v4l2_streamparm parm{};
        parm.type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
        parm.parm.output.timeperframe.numerator   = 1;
        parm.parm.output.timeperframe.denominator = fps > 0 ? fps : 30;
        ioctl(fd_, VIDIOC_S_PARM, &parm);

        width_  = width;
        height_ = height;
        std::cout << "Virtual camera: " << VCAM_DEVICE
                  << " @ " << width << "x" << height
                  << " " << fps << "fps" << std::endl;
        return true;
    }

    void writeFrame(const cv::Mat &bgr) {
        if (fd_ < 0) return;
        cv::Mat yuv;
        if (bgr.cols != width_ || bgr.rows != height_) {
            cv::Mat resized;
            cv::resize(bgr, resized, cv::Size(width_, height_));
            cv::cvtColor(resized, yuv, cv::COLOR_BGR2YUV_I420);
        } else {
            cv::cvtColor(bgr, yuv, cv::COLOR_BGR2YUV_I420);
        }
        ::write(fd_, yuv.data, yuv.total() * yuv.elemSize());
    }

    void writeIdleFrame() {
        if (fd_ < 0) return;
        if (idleYuv_.empty() || idleW_ != width_ || idleH_ != height_) {
            int w = width_  > 0 ? width_  : 1280;
            int h = height_ > 0 ? height_ : 720;
            cv::Mat black = cv::Mat::zeros(h, w, CV_8UC3);
            cv::putText(black, "Camera Off",
                        cv::Point(w / 2 - 120, h / 2),
                        cv::FONT_HERSHEY_SIMPLEX, 1.5,
                        cv::Scalar(80, 80, 80), 2);
            cv::cvtColor(black, idleYuv_, cv::COLOR_BGR2YUV_I420);
            idleW_ = w;
            idleH_ = h;
        }
        ::write(fd_, idleYuv_.data, idleYuv_.total() * idleYuv_.elemSize());
    }

    bool isOpen() const { return fd_ >= 0; }
    int width()  const { return width_; }
    int height() const { return height_; }

private:
    int fd_, width_, height_;
    cv::Mat idleYuv_;
    int idleW_ = 0, idleH_ = 0;
};

// ══════════════════════════════════════════════════════════════════════════
// Preview writer
// ══════════════════════════════════════════════════════════════════════════
static void writePreviewJpeg(const cv::Mat &bgr) {
    static std::vector<int> params = {cv::IMWRITE_JPEG_QUALITY, 80};
    std::vector<uchar> buf;
    cv::imencode(".jpg", bgr, buf, params);

    FILE *f = fopen(PREVIEW_TMP, "wb");
    if (f) {
        fwrite(buf.data(), 1, buf.size(), f);
        fclose(f);
        rename(PREVIEW_TMP, PREVIEW_FILE);
    }
}

// ══════════════════════════════════════════════════════════════════════════
// VideoFX Processor
// ══════════════════════════════════════════════════════════════════════════
class VideoFXProcessor {
public:
    VideoFXProcessor()
        : eff_(nullptr), bgblurEff_(nullptr), artifactEff_(nullptr),
          stream_(nullptr), inited_(false), artifactInited_(false),
          batchOfStates_(nullptr) {}

    ~VideoFXProcessor() { destroy(); }

    bool init(const std::string &modelDir, int mode) {
        NvCV_Status err;

        err = NvVFX_CreateEffect(NVVFX_FX_GREEN_SCREEN, &eff_);
        if (err != NVCV_SUCCESS) {
            std::cerr << "Error creating Green Screen effect: " << err << std::endl;
            return false;
        }

        NvVFX_SetString(eff_, NVVFX_MODEL_DIRECTORY, modelDir.c_str());
        NvVFX_SetU32(eff_, NVVFX_MODE, mode);
        NvVFX_CudaStreamCreate(&stream_);
        NvVFX_SetCudaStream(eff_, NVVFX_CUDA_STREAM, stream_);
        NvVFX_SetU32(eff_, NVVFX_MAX_INPUT_WIDTH, 1920);
        NvVFX_SetU32(eff_, NVVFX_MAX_INPUT_HEIGHT, 1080);
        NvVFX_SetU32(eff_, NVVFX_MAX_NUMBER_STREAMS, 1);

        std::cout << "Loading AI model..." << std::endl;
        err = NvVFX_Load(eff_);
        if (err != NVCV_SUCCESS) {
            std::cerr << "Error loading model: " << err << std::endl;
            return false;
        }
        std::cout << "Model loaded." << std::endl;

        NvVFX_StateObjectHandle state;
        NvVFX_AllocateState(eff_, &state);
        stateArray_.push_back(state);

        // Background blur
        if (NvVFX_CreateEffect(NVVFX_FX_BGBLUR, &bgblurEff_) == NVCV_SUCCESS) {
            NvVFX_SetCudaStream(bgblurEff_, NVVFX_CUDA_STREAM, stream_);
        } else {
            bgblurEff_ = nullptr;
        }

        // Artifact reduction (denoise)
        if (NvVFX_CreateEffect(NVVFX_FX_ARTIFACT_REDUCTION, &artifactEff_) == NVCV_SUCCESS) {
            NvVFX_SetCudaStream(artifactEff_, NVVFX_CUDA_STREAM, stream_);
            NvVFX_SetString(artifactEff_, NVVFX_MODEL_DIRECTORY, modelDir.c_str());
        } else {
            artifactEff_ = nullptr;
        }

        inited_ = true;
        return true;
    }

    // Allocate GPU buffers for a given resolution. Must be called when resolution changes.
    bool allocate(int width, int height) {
        deallocateBuffers();
        NvCV_Status err;
        err = NvCVImage_Alloc(&srcGPU_,  width, height, NVCV_BGR, NVCV_U8, NVCV_CHUNKY, NVCV_GPU, 1);
        if (err != NVCV_SUCCESS) return false;
        err = NvCVImage_Alloc(&dstGPU_,  width, height, NVCV_A,   NVCV_U8, NVCV_CHUNKY, NVCV_GPU, 1);
        if (err != NVCV_SUCCESS) return false;
        err = NvCVImage_Alloc(&blurGPU_, width, height, NVCV_BGR, NVCV_U8, NVCV_CHUNKY, NVCV_GPU, 1);
        if (err != NVCV_SUCCESS) return false;

        NvCVImage_Alloc(&artifactInGPU_,  width, height, NVCV_BGR, NVCV_F32, NVCV_PLANAR, NVCV_GPU, 1);
        NvCVImage_Alloc(&artifactOutGPU_, width, height, NVCV_BGR, NVCV_F32, NVCV_PLANAR, NVCV_GPU, 1);

        unsigned modelBatch = 1;
        NvVFX_GetU32(eff_, NVVFX_MODEL_BATCH, &modelBatch);
        batchOfStates_ = (NvVFX_StateObjectHandle *)malloc(
            sizeof(NvVFX_StateObjectHandle) * modelBatch);
        batchOfStates_[0] = stateArray_[0];

        if (artifactEff_ && !artifactInited_ && artifactInGPU_.pixels && artifactOutGPU_.pixels) {
            NvVFX_SetImage(artifactEff_, NVVFX_INPUT_IMAGE,  &artifactInGPU_);
            NvVFX_SetImage(artifactEff_, NVVFX_OUTPUT_IMAGE, &artifactOutGPU_);
            if (NvVFX_Load(artifactEff_) == NVCV_SUCCESS) {
                artifactInited_ = true;
            }
        }

        bufWidth_  = width;
        bufHeight_ = height;
        return true;
    }

    // outMatte, when provided, receives the person/background segmentation
    // mask (0..255) computed for this frame — empty if mode doesn't run
    // segmentation (MODE_NONE) or on early failure, meaning "no restriction".
    cv::Mat process(const cv::Mat &frame, int mode, cv::Mat *outMatte = nullptr) {
        if (outMatte) *outMatte = cv::Mat();
        if (!inited_ || mode == MODE_NONE) return frame.clone();
        if (frame.cols != bufWidth_ || frame.rows != bufHeight_) return frame.clone();

        cv::Mat matte = cv::Mat::zeros(frame.size(), CV_8UC1);
        cv::Mat result(frame.rows, frame.cols, CV_8UC3);

        NvCVImage srcW, matteW, resultW;
        NVWrapperForCVMat(&frame,  &srcW);
        NVWrapperForCVMat(&matte,  &matteW);
        NVWrapperForCVMat(&result, &resultW);

        NvVFX_SetImage(eff_, NVVFX_INPUT_IMAGE,  &srcGPU_);
        NvVFX_SetImage(eff_, NVVFX_OUTPUT_IMAGE, &dstGPU_);
        NvCVImage_Transfer(&srcW, &srcGPU_, 1.0f, stream_, NULL);
        NvVFX_SetStateObjectHandleArray(eff_, NVVFX_STATE, batchOfStates_);

        if (NvVFX_Run(eff_, 0) != NVCV_SUCCESS) return frame.clone();
        NvCVImage_Transfer(&dstGPU_, &matteW, 1.0f, stream_, NULL);
        if (outMatte) *outMatte = matte;

        switch (mode) {
        case MODE_MATTE:
            cv::cvtColor(matte, result, cv::COLOR_GRAY2BGR);
            break;

        case MODE_GREEN: {
            const unsigned char bg[3] = {0, 255, 0};
            NvCVImage_CompositeOverConstant(&srcW, &matteW, bg, &resultW, stream_);
            break;
        }
        case MODE_WHITE: {
            const unsigned char bg[3] = {255, 255, 255};
            NvCVImage_CompositeOverConstant(&srcW, &matteW, bg, &resultW, stream_);
            break;
        }
        case MODE_LIGHT:
            for (int y = 0; y < frame.rows; y++) {
                for (int x = 0; x < frame.cols; x++) {
                    float a = matte.at<uchar>(y, x) / 255.0f;
                    auto p = frame.at<cv::Vec3b>(y, x);
                    result.at<cv::Vec3b>(y, x) = cv::Vec3b(
                        p[0] * (0.5f + 0.5f * a),
                        p[1] * (0.5f + 0.5f * a),
                        p[2] * (0.5f + 0.5f * a));
                }
            }
            break;

        case MODE_BG:
            if (!bgImg_.empty()) {
                NvCVImage bgW;
                NVWrapperForCVMat(&bgImg_, &bgW);
                NvCVImage_Composite(&srcW, &bgW, &matteW, &resultW, stream_);
            } else {
                const unsigned char bg[3] = {0, 200, 0};
                NvCVImage_CompositeOverConstant(&srcW, &matteW, bg, &resultW, stream_);
            }
            break;

        case MODE_BLUR:
            if (bgblurEff_) {
                NvVFX_SetF32(bgblurEff_, NVVFX_STRENGTH, g_blurStrength.load());
                NvVFX_SetImage(bgblurEff_, NVVFX_INPUT_IMAGE_0, &srcGPU_);
                NvVFX_SetImage(bgblurEff_, NVVFX_INPUT_IMAGE_1, &dstGPU_);
                NvVFX_SetImage(bgblurEff_, NVVFX_OUTPUT_IMAGE,  &blurGPU_);
                NvVFX_Load(bgblurEff_);
                if (NvVFX_Run(bgblurEff_, 0) == NVCV_SUCCESS) {
                    NvCVImage_Transfer(&blurGPU_, &resultW, 1.0f, stream_, NULL);
                } else {
                    frame.copyTo(result);
                }
            }
            break;

        case MODE_DENOISE:
            if (artifactEff_ && artifactInited_) {
                NvCV_Status err;
                err = NvCVImage_Transfer(&srcW, &artifactInGPU_, 1.0f / 255.0f, stream_, NULL);
                if (err == NVCV_SUCCESS) err = NvVFX_Run(artifactEff_, 0);
                if (err == NVCV_SUCCESS) {
                    NvCVImage_Transfer(&artifactOutGPU_, &resultW, 255.0f, stream_, NULL);
                } else {
                    frame.copyTo(result);
                }
            } else {
                frame.copyTo(result);
            }
            break;

        default:
            frame.copyTo(result);
        }

        return result;
    }

    void setBackground(const std::string &path, int width, int height) {
        bgImg_ = cv::imread(path);
        if (!bgImg_.empty()) {
            cv::resize(bgImg_, bgImg_, cv::Size(width, height));
            std::cout << "Background: " << path << std::endl;
        }
    }

private:
    void deallocateBuffers() {
        NvCVImage_Dealloc(&srcGPU_);
        NvCVImage_Dealloc(&dstGPU_);
        NvCVImage_Dealloc(&blurGPU_);
        NvCVImage_Dealloc(&artifactInGPU_);
        NvCVImage_Dealloc(&artifactOutGPU_);
        if (batchOfStates_) { free(batchOfStates_); batchOfStates_ = nullptr; }
        bufWidth_ = bufHeight_ = 0;
    }

    void destroy() {
        for (auto &s : stateArray_) {
            if (eff_ && s) NvVFX_DeallocateState(eff_, s);
        }
        stateArray_.clear();
        deallocateBuffers();
        if (eff_)         { NvVFX_DestroyEffect(eff_);         eff_ = nullptr; }
        if (bgblurEff_)   { NvVFX_DestroyEffect(bgblurEff_);   bgblurEff_ = nullptr; }
        if (artifactEff_) { NvVFX_DestroyEffect(artifactEff_); artifactEff_ = nullptr; }
        if (stream_)      { NvVFX_CudaStreamDestroy(stream_);  stream_ = nullptr; }
    }

    NvVFX_Handle eff_, bgblurEff_, artifactEff_;
    CUstream stream_;
    bool inited_, artifactInited_;
    int bufWidth_ = 0, bufHeight_ = 0;

    NvCVImage srcGPU_{}, dstGPU_{}, blurGPU_{};
    NvCVImage artifactInGPU_{}, artifactOutGPU_{};
    std::vector<NvVFX_StateObjectHandle> stateArray_;
    NvVFX_StateObjectHandle *batchOfStates_;
    cv::Mat bgImg_;
};

// ══════════════════════════════════════════════════════════════════════════
// Command Listener
// ══════════════════════════════════════════════════════════════════════════
static void commandListener() {
    mkdir(SHARED_DIR, 0777);
    unlink(CMD_PIPE_PATH);
    mkfifo(CMD_PIPE_PATH, 0666);

    while (g_running) {
        // Open read-write to avoid blocking on open when no writer
        int fd = ::open(CMD_PIPE_PATH, O_RDWR | O_NONBLOCK);
        if (fd < 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            continue;
        }

        struct pollfd pfd = {fd, POLLIN, 0};
        while (g_running) {
            int ret = poll(&pfd, 1, 500);
            if (ret < 0) break;
            if (ret == 0) continue;
            if (pfd.revents & (POLLHUP | POLLERR)) break;
            if (!(pfd.revents & POLLIN)) continue;

            char buf[1024];
            ssize_t n = ::read(fd, buf, sizeof(buf) - 1);
            if (n <= 0) break;
            buf[n] = '\0';

            char *line = strtok(buf, "\n");
            while (line) {
                std::string cmd(line);

                if (cmd == "QUIT") {
                    g_running = false;
                } else if (cmd == "WINDOW:visible") {
                    g_windowVisible = true;
                } else if (cmd == "WINDOW:hidden") {
                    g_windowVisible = false;
                } else if (cmd.rfind("MODE:", 0) == 0) {
                    g_effectMode = std::stoi(cmd.substr(5));
                } else if (cmd.rfind("BLUR:", 0) == 0) {
                    g_blurStrength = std::stof(cmd.substr(5));
                } else if (cmd.rfind("ZOOM:", 0) == 0) {
                    float z = std::stof(cmd.substr(5));
                    if (z >= 1.0f && z <= 2.0f) g_zoomFactor = z;
                } else if (cmd.rfind("PANX:", 0) == 0) {
                    float p = std::stof(cmd.substr(5));
                    if (p >= -1.0f && p <= 1.0f) g_panX = p;
                } else if (cmd.rfind("PANY:", 0) == 0) {
                    float p = std::stof(cmd.substr(5));
                    if (p >= -1.0f && p <= 1.0f) g_panY = p;
                } else if (cmd.rfind("AUTOREFRAME:", 0) == 0) {
                    g_autoReframe = (cmd.substr(12) == "1");
                } else if (cmd.rfind("AUTOREFRAME_MODEL:", 0) == 0) {
                    std::string m = cmd.substr(18);
                    if (m == "haar") g_reframeModel = 0;
                    else if (m == "dnn") g_reframeModel = 1;
                } else if (cmd.rfind("AUTOREFRAME_ZOOM:", 0) == 0) {
                    float v = std::stof(cmd.substr(17));
                    if (v >= 1.0f && v <= 2.0f) g_reframeMinZoom = v;
                } else if (cmd.rfind("AUTOREFRAME_SPEED:", 0) == 0) {
                    float v = std::stof(cmd.substr(19));
                    if (v >= 0.5f && v <= 0.95f) g_reframeSmoothing = v;
                } else if (cmd.rfind("VIRTUALLIGHT_INTENSITY:", 0) == 0) {
                    float v = std::stof(cmd.substr(23));
                    if (v >= 0.0f && v <= 1.0f) g_virtualLightIntensity = v;
                } else if (cmd.rfind("VIRTUALLIGHT:", 0) == 0) {
                    g_virtualLight = (cmd.substr(13) == "1");
                } else if (cmd.rfind("BG:", 0) == 0) {
                    std::lock_guard<std::mutex> lock(g_bgMutex);
                    g_bgFile = cmd.substr(3);
                    g_bgChanged = true;
                } else if (cmd.rfind("DEVICE:", 0) == 0) {
                    std::lock_guard<std::mutex> lock(g_deviceMutex);
                    std::string dev = cmd.substr(7);
                    if (dev != g_inputDevice) {
                        g_inputDevice = dev;
                        g_deviceChanged = true;
                    }
                } else if (cmd.rfind("RESOLUTION:", 0) == 0) {
                    std::string res = cmd.substr(11);
                    auto x = res.find('x');
                    if (x != std::string::npos) {
                        int w = std::stoi(res.substr(0, x));
                        int h = std::stoi(res.substr(x + 1));
                        if (w > 0 && h > 0) {
                            g_cameraWidth  = w;
                            g_cameraHeight = h;
                            g_cameraSettingsChanged = true;
                        }
                    }
                } else if (cmd.rfind("FPS:", 0) == 0) {
                    int fps = std::stoi(cmd.substr(4));
                    if (fps > 0 && fps <= 120) {
                        g_cameraFps = fps;
                        g_cameraSettingsChanged = true;
                    }
                }

                line = strtok(nullptr, "\n");
            }
        }
        close(fd);
    }
    unlink(CMD_PIPE_PATH);
}

// ══════════════════════════════════════════════════════════════════════════
// Camera auto-detection
// ══════════════════════════════════════════════════════════════════════════
static std::string autoDetectCamera() {
    for (int i = 0; i <= 9; i++) {
        std::string path = "/dev/video" + std::to_string(i);
        if (path == VCAM_DEVICE) continue;
        struct stat st;
        if (stat(path.c_str(), &st) != 0) continue;
        cv::VideoCapture test;
        test.open(path, cv::CAP_V4L2);
        if (test.isOpened()) {
            test.release();
            return path;
        }
    }
    return "";
}

// ══════════════════════════════════════════════════════════════════════════
// Main loop
// ══════════════════════════════════════════════════════════════════════════
static void signalHandler(int) { g_running = false; }

int main(int argc, char **argv) {
    signal(SIGINT,  signalHandler);
    signal(SIGTERM, signalHandler);

    setenv("OPENCV_VIDEOIO_PRIORITY_V4L2",     "990", 0);
    setenv("OPENCV_VIDEOIO_PRIORITY_GSTREAMER", "0",   0);

    // Auto Reframe and Virtual Light each hold their own cv::dnn::Net; by
    // default OpenCV's DNN backend fans a single forward() call out across
    // every logical core. With two nets potentially running per frame, that
    // caused severe thread oversubscription (~1000% CPU, near-frozen video)
    // instead of the expected roughly-additive cost. Capping it keeps both
    // detectors affordable together.
    cv::setNumThreads(4);

    std::string modelDir = "/usr/local/VideoFX/lib/models";
    int aiMode = 0;
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg.rfind("--model_dir=", 0) == 0)  modelDir = arg.substr(12);
        else if (arg.rfind("--mode=", 0) == 0)   aiMode = std::stoi(arg.substr(7));
        else if (arg == "--performance" || arg == "-p") aiMode = 1;
    }

    std::cout << "════════════════════════════════════" << std::endl;
    std::cout << "           BluCast Server" << std::endl;
    std::cout << "════════════════════════════════════" << std::endl;
    std::cout << "Model dir: " << modelDir << std::endl;
    std::cout << "AI mode:   " << (aiMode == 0 ? "Quality" : "Performance") << std::endl;

    writePidFile();

    bool haarOk = g_reframer.initHaar(FACE_CASCADE_PATH);
    bool dnnOk = g_reframer.initDnn(FACE_MODEL_PROTOTXT, FACE_MODEL_WEIGHTS);
    if (!haarOk && !dnnOk) {
        std::cerr << "AutoReframe: no face detector available, feature will be a no-op" << std::endl;
    }
    if (!g_virtualLightFx.init(FACE_MODEL_PROTOTXT, FACE_MODEL_WEIGHTS)) {
        std::cerr << "VirtualLight: face detector unavailable, feature will be a no-op" << std::endl;
    }

    std::thread cmdThread(commandListener);

    VideoFXProcessor vfx;
    if (!vfx.init(modelDir, aiMode)) {
        std::cerr << "Failed to initialize VideoFX" << std::endl;
        g_running = false;
        cmdThread.join();
        return 1;
    }

    VirtualCamera vcam;
    int vcamW = g_cameraWidth.load();
    int vcamH = g_cameraHeight.load();
    int vcamFps = g_cameraFps.load();
    vcam.open(vcamW, vcamH, vcamFps);
    vcam.writeIdleFrame();

    cv::VideoCapture cap;
    bool cameraActive = false;
    bool buffersReady = false;
    int curWidth = 0, curHeight = 0;
    std::string currentDevice;
    bool lastNeedCamera = false;

    std::cout << "Ready. Listening on " << CMD_PIPE_PATH << std::endl;

    while (g_running) {
        int consumers = readConsumerCount();
        bool windowVis = g_windowVisible.load();
        bool needCamera = windowVis || (consumers > 0);

        if (needCamera != lastNeedCamera) {
            std::cout << (needCamera ? "Camera: activating" : "Camera: going idle") << std::endl;
            lastNeedCamera = needCamera;
        }

        if (!needCamera) {
            if (cameraActive) {
                cap.release();
                cameraActive = false;
                std::cout << "Camera released" << std::endl;
            }
            if (vcam.isOpen()) {
                vcam.writeIdleFrame();
            }
            unlink(PREVIEW_FILE);
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
            continue;
        }

        if (!cameraActive) {
            {
                std::lock_guard<std::mutex> lock(g_deviceMutex);
                if (!g_inputDevice.empty()) currentDevice = g_inputDevice;
                g_deviceChanged = false;
            }

            if (currentDevice.empty()) {
                currentDevice = autoDetectCamera();
                if (!currentDevice.empty()) {
                    std::cout << "Auto-detected camera: " << currentDevice << std::endl;
                }
            }

            if (!currentDevice.empty()) {
                cap.open(currentDevice, cv::CAP_V4L2);
            } else {
                cap.open(0, cv::CAP_V4L2);
            }

            if (!cap.isOpened()) {
                std::cerr << "Cannot open camera" << std::endl;
                std::this_thread::sleep_for(std::chrono::seconds(1));
                continue;
            }

            int reqW   = g_cameraWidth.load();
            int reqH   = g_cameraHeight.load();
            int reqFps = g_cameraFps.load();
            cap.set(cv::CAP_PROP_FRAME_WIDTH,  reqW);
            cap.set(cv::CAP_PROP_FRAME_HEIGHT, reqH);
            cap.set(cv::CAP_PROP_FPS,          reqFps);

            curWidth  = (int)cap.get(cv::CAP_PROP_FRAME_WIDTH);
            curHeight = (int)cap.get(cv::CAP_PROP_FRAME_HEIGHT);
            std::cout << "Camera: " << curWidth << "x" << curHeight << std::endl;

            // Reallocate GPU buffers if resolution changed
            if (!buffersReady || curWidth != vcamW || curHeight != vcamH) {
                vfx.allocate(curWidth, curHeight);
                buffersReady = true;
            }

            vcam.open(curWidth, curHeight, reqFps);
            vcamW = curWidth;
            vcamH = curHeight;
            vcamFps = reqFps;

            cameraActive = true;
        }

        {
            std::lock_guard<std::mutex> lock(g_deviceMutex);
            if (g_deviceChanged) {
                g_deviceChanged = false;
                cap.release();
                cameraActive = false;
                buffersReady = false;
                continue;
            }
        }
        if (g_cameraSettingsChanged.exchange(false)) {
            cap.release();
            cameraActive = false;
            buffersReady = false;
            continue;
        }

        {
            std::lock_guard<std::mutex> lock(g_bgMutex);
            if (g_bgChanged && !g_bgFile.empty()) {
                vfx.setBackground(g_bgFile, curWidth, curHeight);
                g_bgChanged = false;
            }
        }

        cv::Mat frame;
        cap >> frame;
        if (frame.empty()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            continue;
        }
        float zoom = g_zoomFactor.load();
        float panX = g_panX.load();
        float panY = g_panY.load();
        if (g_autoReframe.load()) {
            try {
                g_reframer.setModel(static_cast<ReframeModel>(g_reframeModel.load()));
                g_reframer.setMinZoom(g_reframeMinZoom.load());
                g_reframer.setSmoothing(g_reframeSmoothing.load());
                float autoZoom, autoPanX, autoPanY;
                if (g_reframer.update(frame, autoZoom, autoPanX, autoPanY)) {
                    zoom = autoZoom;
                    panX = autoPanX;
                    panY = autoPanY;
                }
            } catch (const std::exception &e) {
                std::cerr << "AutoReframe error, skipping this frame: " << e.what() << std::endl;
            }
        }
        applyZoom(frame, zoom, panX, panY);

        int mode = g_effectMode.load();
        cv::Mat matte;
        cv::Mat result;
        try {
            result = vfx.process(frame, mode, &matte);

            // Applied after compositing, gated by the segmentation matte, so
            // the light only ever touches the actual person — never the
            // background that just got replaced/blurred behind them.
            if (g_virtualLight.load()) {
                g_virtualLightFx.apply(result, g_virtualLightIntensity.load(), &matte);
            }
        } catch (const std::exception &e) {
            std::cerr << "Frame processing error, using unprocessed frame: " << e.what() << std::endl;
            result = frame.clone();
        }

        vcam.writeFrame(result);

        if (windowVis) {
            writePreviewJpeg(result);
        }
    }

    if (cameraActive) cap.release();
    unlink(PID_FILE);
    unlink(PREVIEW_FILE);
    unlink(PREVIEW_TMP);

    g_running = false;
    cmdThread.join();
    std::cout << "BluCast closed." << std::endl;
    return 0;
}
