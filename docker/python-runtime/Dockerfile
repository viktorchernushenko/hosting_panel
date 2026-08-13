FROM python:3.12-alpine
RUN pip install --no-cache-dir gunicorn==23.0.0
USER 65534:65534
WORKDIR /app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
