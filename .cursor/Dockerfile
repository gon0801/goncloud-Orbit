# Imagen base del entorno de desarrollo de Orbit para Cloud Agents.
#
# Trae SOLO las dependencias de sistema estables (Postgres 16 + toolchain de
# Python + uv). Las dependencias del proyecto las instala `uv sync` en la fase
# `install` (que ya ve el uv.lock del codigo checkouteado) y Postgres arranca
# en la fase `start` (.cursor/start.sh). No se copia el repo aca: Cursor lo
# checkoutea aparte, y meterlo en la imagen romperia el cache y la logica de
# ramas.
#
# Postgres 16 = misma version que produccion (docker-compose.yml) y que CI
# (.github/workflows/quality.yml): los tests de integracion aplican
# migrations/0001..0003 contra un Postgres real, asi que el motor tiene que
# estar en la imagen. x86_64 Ubuntu 24.04 = plataforma del VM de Cloud Agents.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        sudo \
        build-essential \
        python3 \
        python3-venv \
        python3-dev \
        python-is-python3 \
        postgresql-16 \
        postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

# uv: gestor de paquetes pinneado por el repo (uv.lock). Se instala a un path
# del sistema para que este en el PATH de cualquier usuario sin tocar perfiles.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && uv --version

# Usuario del agente. La imagen ubuntu:24.04 ya trae `ubuntu` (uid 1000), pero
# lo garantizamos por si el base cambia. Sudo sin password: la fase `start`
# arranca el cluster de Postgres como root (pg_ctlcluster) y crea el rol.
RUN id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ubuntu \
    && chmod 0440 /etc/sudoers.d/90-ubuntu

USER ubuntu
