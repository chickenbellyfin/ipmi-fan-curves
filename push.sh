#!/bin/bash
docker buildx build . -t ghcr.io/chickenbellyfin/ipmi-fan-curve --platform linux/amd64 --push