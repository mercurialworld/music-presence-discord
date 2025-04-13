FROM python:3.12

WORKDIR /
ADD requirements.txt .
RUN pip install -r requirements.txt
ADD bot.py .
ADD Class ./Class
ADD Enum ./Enum
ADD Utils ./Utils

VOLUME [ "/data" ]
WORKDIR /data

CMD ["python", "-u", "/bot.py"] 
