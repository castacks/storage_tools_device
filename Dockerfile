FROM python:3.9-slim

RUN apt-get update -y && apt-get install -y ffmpeg curl

# check out https://github.com/foxglove/mcap/releases for the most recent release! 
# Set MCAP version
ARG MCAP_VERSION=v0.0.48

RUN ARCH="$(uname -m)" && \
    if [ "${ARCH}" = "x86_64" ]; then \
        ARCH="amd64"; \
    elif [ "${ARCH}" = "aarch64" ]; then \
        ARCH="arm64"; \
    else \
        echo "Unsupported architecture ${ARCH}"; exit 1; \
    fi && \
    curl -L "https://github.com/foxglove/mcap/releases/download/releases%2Fmcap-cli%2F${MCAP_VERSION}/mcap-linux-${ARCH}" -o /usr/local/bin/mcap && \
    chmod +x /usr/local/bin/mcap


WORKDIR /tmp
COPY requirements.txt /tmp/

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt


# Set the working directory to /app
WORKDIR /app

# Copy the source files file
COPY device device
COPY config config

# Run the command to start the app when the container starts
CMD ["python", "device/app.py", "-c", "/app/config/config.yaml"]
