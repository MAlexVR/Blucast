# Runtime libraries + AI models (Maxine VideoFX v0.7.2.0) come from the
# already-published image instead of a manual SDK download: the API headers
# are vendored (MIT-licensed) in app/third_party/, and the compiled .so's are
# proprietary NVIDIA binaries we don't have the right to redistribute as a
# separate downloadable package, but reusing them from an image already
# published under the project's own license is fine.
FROM ghcr.io/andrei9383/blucast:latest AS runtime-libs

FROM docker.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu20.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    cmake build-essential libopencv-dev libv4l-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=runtime-libs /usr/local/lib/blucast /usr/local/lib/blucast

WORKDIR /build
COPY app/ /app/

RUN mkdir -p /build/blucast && cd /build/blucast && \
    cmake /app && \
    make -j$(nproc)

RUN gcc -shared -fPIC -o /build/libcc_spoof.so /app/cc_spoof.c -ldl

FROM docker.io/nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu20.04

LABEL maintainer="BluCast"
LABEL description="AI-powered virtual camera with NVIDIA VideoFX"
LABEL org.opencontainers.image.source="https://github.com/Andrei9383/BluCast"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    libopencv-videoio4.2 libopencv-imgproc4.2 libopencv-highgui4.2 \
    libopencv-objdetect4.2 libopencv-dnn4.2 opencv-data \
    python3 python3-pip v4l-utils \
    libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-keysyms1 \
    libxcb-shape0 libegl1 libgl1-mesa-glx libxkbcommon0 libxkbcommon-x11-0 \
    libdbus-1-3 fonts-ubuntu fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir PySide6 numpy

COPY --from=builder /build/blucast/blucast_server /app/blucast_server
COPY --from=builder /build/libcc_spoof.so /usr/local/lib/blucast/libcc_spoof.so
COPY --from=runtime-libs /usr/local/lib/blucast /usr/local/lib/blucast
COPY --from=runtime-libs /usr/local/VideoFX/lib/models /usr/local/VideoFX/lib/models

RUN ln -sf /usr/local/lib/blucast/libVideoFX.so /usr/local/lib/blucast/libNVVideoEffects.so

COPY app/control_panel.py     /app/
COPY app/models/              /app/models/
COPY assets/                  /app/assets/

ENV LD_LIBRARY_PATH=/usr/local/lib/blucast:$LD_LIBRARY_PATH
WORKDIR /app

RUN mkdir -p /tmp/blucast /root/.config/blucast

RUN echo '#!/bin/bash\n\
set -e\n\
echo "Starting BluCast server..."\n\
LD_PRELOAD=/usr/local/lib/blucast/libcc_spoof.so /app/blucast_server --model_dir=/usr/local/VideoFX/lib/models &\n\
SERVER_PID=$!\n\
\n\
# Wait for the command pipe to be created\n\
for i in $(seq 1 30); do\n\
    [ -p /tmp/blucast/cmd.pipe ] && break\n\
    sleep 0.5\n\
done\n\
\n\
echo "Starting BluCast GUI..."\n\
python3 /app/control_panel.py\n\
EXIT_CODE=$?\n\
\n\
# Cleanup: kill server\n\
kill $SERVER_PID 2>/dev/null || true\n\
wait $SERVER_PID 2>/dev/null || true\n\
exit $EXIT_CODE\n\
' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]
