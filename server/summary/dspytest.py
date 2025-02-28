import dspy
from decouple import config  # Load environment variables

# Load API key from .env
openai_api_key = config("OPENAI_KEY")

# ✅ Use `dspy.OpenAI(model="gpt-4o-mini")` instead of `dspy.LM`
lm = dspy.LM(model="gpt-4o-mini", api_key=openai_api_key)
dspy.configure(lm=lm)

# ✅ Test DSPy by running a simple task
math_task = dspy.Predict("question -> answer: float")
result = math_task(question="What is 2 + 2?")

print(result)  # Should return a prediction
