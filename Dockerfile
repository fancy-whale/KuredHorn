# Use the official Python image as the base image
FROM python:3.12

# Set the working directory inside the container
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.7.21 /uv /uvx /bin/

# Copy the dependency metadata to the working directory
COPY pyproject.toml uv.lock /app/

# Install project dependencies
RUN uv sync --locked --no-dev --no-install-project

# Copy the rest of the project files to the working directory
COPY ./kuredhorn /app/kuredhorn

# Set the entrypoint to run the script
ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "kuredhorn"]
