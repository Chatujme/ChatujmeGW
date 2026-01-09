FROM python:3.12-slim

LABEL maintainer="LuRy <lury@lury.cz>"
LABEL description="IRC Gateway for Chatujme.cz"
LABEL version="3.0.1"

WORKDIR /app

# Copy application
COPY chatujmegw.py .

# Create non-root user for security
RUN useradd -r -s /bin/false chatujmegw && \
    chown -R chatujmegw:chatujmegw /app

USER chatujmegw

# Plain IRC and SSL ports
EXPOSE 6667 6697

# Health check - verify port is listening
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',6667)); s.close()" || exit 1

ENTRYPOINT ["python3", "-u", "chatujmegw.py"]
CMD ["--port", "6667", "--listen", "0.0.0.0"]
