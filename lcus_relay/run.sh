#!/usr/bin/with-contenv bashio

export SERIAL_PORT=$(bashio::config 'serial_port')
export POLL_INTERVAL=$(bashio::config 'poll_interval')

python3 /app/lcus.py
