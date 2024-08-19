ARG ROS_DISTRO=jazzy

# Use official ros2 image as base image
FROM osrf/ros:${ROS_DISTRO}-desktop

RUN apt-get update && apt-get install -y python3.12-venv ffmpeg 

ENV ROS_DISTRO=${ROS_DISTRO}

ARG CUSTOM_UID=1002
ENV CUSTOM_UID=${CUSTOM_UID}

ARG CUSTOM_GID=1002
ENV CUSTOM_GID=${CUSTOM_GID}

ARG CUSTOM_USERNAME="devscanner"
ENV CUSTOM_USERNAME=${CUSTOM_USERNAME}

RUN echo $CUSTOM_UID
RUN echo ${CUSTOM_GID}
RUN if [ -z "$(getent group  ${CUSTOM_GID})" ] ; then addgroup --gid ${CUSTOM_GID} ${CUSTOM_USERNAME}; else echo "GID: ${CUSTOM_GID} already exists"; fi
RUN if [ -z "$(getent passwd ${CUSTOM_UID})" ] ; then adduser --uid ${CUSTOM_UID} --gid ${CUSTOM_GID} --disabled-password --gecos '' ${CUSTOM_USERNAME}; else echo "UID: ${CUSTOM_UID} already exists"; fi



RUN adduser ${CUSTOM_USERNAME} sudo
RUN echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers


USER ${CUSTOM_USERNAME}

# Set the working directory to /app
WORKDIR /home/${CUSTOM_USERNAME}/app

# Copy the requirements file
COPY requirements.txt .

# Install the dependencies
RUN python3 -m venv --system-site-packages --symlinks venv && ./venv/bin/pip3 install --no-cache-dir -r requirements.txt

COPY device device
COPY config config

# set the virtual env to run from the bashrc 
RUN echo "source /home/${CUSTOM_USERNAME}/app/venv/bin/activate" >> /home/${CUSTOM_USERNAME}/.bashrc 


# Run the command to start the app when the container starts
CMD ["/home/devscanner/app/venv/bin/python", "device/app.py", "-c", "/home/devscanner/app/config/config.yaml"]