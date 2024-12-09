FROM python:3.10.15 as base

USER root

COPY requirements.txt /src/requirements.txt
RUN pip install -r /src/requirements.txt --no-cache-dir


COPY compare-org-scim-users.py /src/compare-org-scim-users.py


#execute the compare-org-scim-users.py
ENTRYPOINT ["python", "/src/compare-org-scim-users.py"]





