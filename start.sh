#!/bin/bash
# Script de arranque para Render/Railway.
# El backend necesita correr desde backend/ para que los imports relativos funcionen.
set -e
cd backend
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
