#!/bin/bash

export CURRENT_UID=$(id -u)
export CURRENT_GID=$(id -g)

echo "Docker will run as UID=$CURRENT_UID GID=$CURRENT_GID"
