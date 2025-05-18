# Start with an official Python base image
FROM python:3.11-slim-buster

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for Chrome, Selenium, and Pillow
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    # Chrome dependencies
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libexpat1 libgbm1 libgcc1 libstdc++6 libx11-6 \
    libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libasound2 \
    # Pillow dependencies
    libjpeg62-turbo-dev \
    zlib1g-dev \
    # Clean up apt cache
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome Stable
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy your Python script into the container
COPY sword1.py ./
# If sword1.py imports other local .py files you created, copy them too:
# COPY other_helper_script.py ./

# Command to run your Python script when the container starts
CMD ["python3", "sword1.py"]