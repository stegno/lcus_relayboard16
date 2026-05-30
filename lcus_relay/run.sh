#!/usr/bin/with-contenv bashio

export SERIAL_PORT=$(bashio::config 'serial_port')
export POLL_INTERVAL=$(bashio::config 'poll_interval')

export MQTT_HOST=$(bashio::services mqtt "host")
export MQTT_PORT=$(bashio::services mqtt "port")
export MQTT_USER=$(bashio::services mqtt "username")
export MQTT_PASSWORD=$(bashio::services mqtt "password")

python3 /app/lcus.py
