FF Factory
========

This is a small mostly vibe-coded project because I want a GUI for `ffmpeg`, i.e. something like Format Factory.

The `app.py` file is vibe coded and I made some small changes, which I made sure to show by the commits made.

Then I added a very simple `Dockerfile` and `docker-compose.yml` so you can deploy it with [docker compose](https://docs.docker.com/compose/). To deploy, simply execute:

```bash
docker compose up -d
```

Then you should be able to access it in your browser by visiting `http://localhost:7860`. It may take some time to build for the first time.

This isn't fully tested, so use it at your own discretion! (LOL)
