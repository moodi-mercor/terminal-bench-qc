FROM python:3.11-slim
WORKDIR /app
COPY generate_answer.py /app/generate_answer.py
# bake the dataset + answer at build, then hide the generator source
RUN python3 /app/generate_answer.py && rm -f /app/generate_answer.py
