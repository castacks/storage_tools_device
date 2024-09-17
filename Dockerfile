FROM python:3.9-slim

RUN apt-get update && apt-get install -y ffmpeg 

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
