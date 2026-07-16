FROM python:3.12-slim

LABEL maintainer="LuRy <lury@lury.cz>"
LABEL description="IRC Gateway for Chatujme.cz"

WORKDIR /app

# Copy application
COPY chatujmegw.py .

# Create non-root user for security
RUN useradd -r -s /bin/false chatujmegw && \
    chown -R chatujmegw:chatujmegw /app

USER chatujmegw

# Plain IRC and SSL ports
EXPOSE 6667 6697

# Health check - verify python process is running (no TCP spam)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test -d /proc/1/fd || exit 1

ENTRYPOINT ["python3", "-u", "chatujmegw.py"]
CMD ["--port", "6667", "--listen", "0.0.0.0"]
