FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
ENV FLASK_APP=lively_marketplace_app.py
EXPOSE 5000
CMD ["flask", "run", "--host=0.0.0.0"]
