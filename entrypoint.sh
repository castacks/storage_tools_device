#!/bin/sh

# gunicorn isn't playing nice with multiprocess.Pool.  
# exec gunicorn -k gthread -w 1 --threads 4 -b "0.0.0.0:${CONFIG_PORT}" --timeout 240 "device.app:app" --env CONFIG_PORT="${CONFIG_PORT}"
exec python -m device.app