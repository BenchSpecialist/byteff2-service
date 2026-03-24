# ==========================================
# Stage 1: Builder (Compilers & Tools)
# ==========================================
FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install heavy build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    swig \
    python3 \
    python3-pip \
    python3-dev \
    doxygen \
    clang \
    && rm -rf /var/lib/apt/lists/*

# Install Python build dependencies
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir numpy cython

# Copy source code
COPY ./submodules /app/byteff2/submodules

# Build OpenMM
# This runs the build and typically installs artifacts to /usr/local/openmm
# and Python bindings to /usr/local/lib/python3.10/dist-packages
RUN GIT_COMMITTER_NAME="Builder" GIT_COMMITTER_EMAIL="builder@example.com" \
    bash -ex /app/byteff2/submodules/openmm/install.sh

# Install requirements.txt in the builder stage
# We do this here to ensure any packages requiring compilation (C/C++ extensions) build correctly
COPY ./requirements.txt .

RUN pip3 install --no-cache-dir -r /app/requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    && rm /app/requirements.txt


# ==========================================
# Stage 2: Runtime (Final Image)
# ==========================================
# Use the 'runtime' tag (much smaller than 'devel')
FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install only runtime system dependencies
# We include python3, gromacs, and the shared libraries you listed
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    libxext6 \
    libsm6 \
    libxrender1 \
    gromacs \
    git \
    && rm -rf /var/lib/apt/lists/*

# Verify GROMACS installation
RUN gmx --version | head -n 20 || exit 1

# 1. Copy the compiled OpenMM library from the builder
COPY --from=builder /usr/local/openmm /usr/local/openmm

# 2. Copy the installed Python packages (including OpenMM bindings, numpy, and requirements)
# Note: Ubuntu 22.04 uses Python 3.10. If the base python version changes, update this path.
COPY --from=builder /usr/local/lib/python3.10/dist-packages /usr/local/lib/python3.10/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 3. Copy the application source code
COPY . /app/byteff2

# Set environment variables
ENV LD_LIBRARY_PATH=/usr/local/openmm/lib:${LD_LIBRARY_PATH}
ENV PATH=/usr/local/openmm/bin:${PATH}
ENV PYTHONPATH=/app/byteff2:${PYTHONPATH}
ENV WORKSPACE_DIR=/app/workspace

CMD ["/bin/bash"]