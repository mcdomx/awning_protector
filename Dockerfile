FROM python:3.9-slim

WORKDIR /app

RUN pip install --no-cache-dir pipenv

COPY Pipfile Pipfile.lock* ./
RUN pipenv install --deploy --system --ignore-pipfile || pipenv install --system

COPY . .

RUN mkdir -p /app/data

EXPOSE 8767

ENTRYPOINT ["python", "main.py"]
