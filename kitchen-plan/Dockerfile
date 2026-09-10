FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . ./

RUN python -m compileall -q src

CMD ["python", "-m", "src"]
