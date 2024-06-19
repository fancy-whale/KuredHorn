# Use the official Python image as the base image
FROM python:3.11

# Set the working directory inside the container
WORKDIR /app

# Copy the poetry.lock and pyproject.toml files to the working directory
COPY poetry.lock pyproject.toml /app/

# Install Poetry
RUN pip install poetry

# Install project dependencies without creating a virtual environment and without installing the dev group
RUN poetry install --no-root --no-dev

# Copy the rest of the project files to the working directory
COPY ./kuredhorn /app/kuredhorn

# Set the entrypoint to run the script
ENTRYPOINT ["poetry", "run", "python", "-m", "kuredhorn"]