#!/bin/sh

exec gunicorn -k gthread -w 1 --threads 4 -b "0.0.0.0:${CONFIG_PORT}" --timeout 240 "device.app:app"
