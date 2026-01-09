FROM python:3.11-slim

LABEL maintainer="LuRy <lury@lury.cz>"
LABEL description="IRC Gateway for Chatujme.cz"

WORKDIR /app

# Copy application
COPY chatujmegw.py .

# Create non-root user for security
RUN useradd -r -s /bin/false chatujmegw && \
    chown -R chatujmegw:chatujmegw /app

USER chatujmegw

EXPOSE 6667

ENTRYPOINT ["python3", "chatujmegw.py"]
CMD ["--port", "6667", "--listen", "0.0.0.0"]
