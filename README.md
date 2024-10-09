# Deposum: an LLM-based Deposition Summary Tool and Chatbot
## About
Deposum is a Django-based web application designed to help lawyers (or other interested parties) quickly go through a court deposition. It uses one of OpenAI's GPT models (which can be customized) to summarize given depositions and to run a chatbot which can be used to ask questions about the deposition. **Note that this tool is currently a work-in-progress, and is not yet ready for full deployment.**
## Running locally
In order to run this website locally, a `.env` file is required to configure certain settings. It should be formatted as a newline-separated list of `key=value` pairs, and should contain the following:

- `OPENAI_KEY`: the OpenAI API key
- `GPT_MODEL`: the name of the GPT model to use (`gpt-4o-mini` used in testing)
- `CHROMA_URL`: the name of the directory where Chroma vector databases will be stored
- `SUMMARY_URL`: the name of the directory where generated summaries will be stored
- `DEPO_URL`: the name of the directory where given depositions will be stored
- `SECRET_KEY`: the secret key to be used by Django in production

In addition, this repository contains a script called `hourly.py`, which should be run hourly when the web server is active.
