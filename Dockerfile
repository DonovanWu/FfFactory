FROM python:3.12-slim

RUN apt update && apt install -y ffmpeg && pip install gradio==6.6.0

ENV GRADIO_SERVER_NAME=0.0.0.0
ENV GRADIO_SERVER_PORT=7860

COPY ./ /app
WORKDIR /app

CMD ["python", "app.py"]
EXPOSE 7860
