# Forge Studio Standalone — container image.
#
# Studio is the product; Forge Neo is the bundled compute engine. This image
# runs the Studio HTTP server and nothing else. There is no Gradio, no Forge
# WebUI, and no second server: `launch_studio.py --config` is the one entry
# point, exactly as on a workstation.
#
# Deliberate choices, each of which would be a defect if made the other way:
#
#   NON-ROOT          the container writes to results and one config file and
#                     reads model directories. None of that needs root, and a
#                     bind-mounted model library owned by the host user is a
#                     library a root process can damage.
#
#   NO MODELS BAKED   models are mounted, never copied in. Baking them would
#                     make the image enormous, unshareable, and a copy of
#                     someone's licensed weights.
#
#   0.0.0.0 INSIDE    a container's loopback is its own. Binding 127.0.0.1
#                     here would make the port unreachable even from the host
#                     that published it. The exposure decision belongs to the
#                     `-p` on the host side, which compose pins to 127.0.0.1.
#
#   CPU BASE          the default image carries no CUDA. GPU is opt-in through
#                     the build argument below, because an image that assumes
#                     CUDA cannot run on a machine without it -- and macOS and
#                     plain Linux hosts are first-class targets.
#
# Build:  docker build -t forge-studio .        (context = the repository root)
# Run:    docker compose up

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS runtime

# Pinned by the project's requirements.txt; the index is a build argument so a
# GPU build points at the CUDA wheels without editing this file.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STUDIO_HOME=/studio

# `libgl1` and `libglib2.0-0` are what opencv-python needs to import at all;
# without them the first Studio import fails with a bare shared-object error
# that says nothing about the cause.
RUN apt-get update \
 && apt-get install --no-install-recommends -y \
      libgl1 libglib2.0-0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# A real user, created before anything is copied, so ownership is right the
# first time rather than fixed up by a recursive chown of the whole tree.
RUN groupadd --gid 1000 studio \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin studio

WORKDIR ${STUDIO_HOME}

# Dependencies first, as their own layer: the application changes constantly
# and the dependency set does not, so a code edit must not re-resolve torch.
COPY --chown=studio:studio requirements.txt ./app/requirements.txt
RUN python -m pip install --upgrade pip \
 && python -m pip install --extra-index-url "${TORCH_INDEX_URL}" \
      -r app/requirements.txt

COPY --chown=studio:studio . ./app
COPY --chown=studio:studio deploy/entrypoint.py ./deploy/entrypoint.py

# The three mount points. Created and owned here so a run with no bind mount
# still starts, and a bind mount lands on a directory that already has the
# right owner.
#
# `models` is read-only in compose. Studio never writes to a model directory,
# and the mount is where that is enforced rather than merely intended.
RUN mkdir -p /studio/config /studio/results /studio/models \
 && chown -R studio:studio /studio/config /studio/results /studio/models

USER studio

# Inside the container, not on the host. The host publish in compose is what
# decides who can reach it, and that is pinned to 127.0.0.1.
EXPOSE 7865

# `/api/status` answers without a model resident and without touching the
# GPU, so a healthy container is one that is SERVING -- not one that has
# loaded something.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "/studio/deploy/entrypoint.py", "--health"]

ENTRYPOINT ["python", "/studio/deploy/entrypoint.py"]
