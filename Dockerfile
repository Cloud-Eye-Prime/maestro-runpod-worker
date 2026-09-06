FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BLENDER_VERSION=4.2.23 \
    BLENDER_SHA256=bea0eb3146be13eae6225409a117b215184f41b7f79e799f97cb3abb8f6dc404

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        wget \
        ca-certificates \
        libxrender1 \
        libxi6 \
        libxkbcommon0 \
        libxfixes3 \
        libxxf86vm1 \
        libgl1 \
        libsm6 \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q -O /tmp/blender.tar.xz \
        "https://download.blender.org/release/Blender4.2/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
    && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
    && mkdir -p /opt/blender \
    && tar -xf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && ln -s /opt/blender/blender /usr/local/bin/blender

RUN pip3 install --no-cache-dir runpod requests

WORKDIR /app
COPY handler.py scene_builder.py /app/

CMD ["python3", "-u", "handler.py"]
