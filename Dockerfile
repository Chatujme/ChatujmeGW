FROM python:3.12-slim

LABEL maintainer="LuRy <lury@lury.cz>"
LABEL description="IRC Gateway for Chatujme.cz"

WORKDIR /app

# Copy application
COPY chatujmegw.py .
COPY src/ ./src/
ENV PYTHONDONTWRITEBYTECODE=1

# Create non-root user for security
RUN useradd -r -s /bin/false chatujmegw && \
    chown -R chatujmegw:chatujmegw /app

USER chatujmegw

# Plain IRC and SSL ports
EXPOSE 6667 6697

# Health check - actually connect to the IRC port and read the greeting, so a
# hung/deadlocked gateway (process alive but not accepting) is reported unhealthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import socket,sys; s=socket.create_connection(('127.0.0.1',6667),5); sys.exit(0 if s.recv(16) else 1)" || exit 1

ENTRYPOINT ["python3", "-u", "chatujmegw.py"]
CMD ["--port", "6667", "--listen", "0.0.0.0"]
