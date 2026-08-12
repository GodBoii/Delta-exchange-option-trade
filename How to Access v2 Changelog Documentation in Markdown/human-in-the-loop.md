# Agno HITL Documentation

## [Overview](https://docs.agno.com/hitl/overview)

Human-in-the-Loop (HITL) in Agno enables you to implement patterns where human oversight and input are required during agent execution. This is crucial for:

* Validating sensitive operations
* Reviewing tool calls before execution
* Gathering user input for decision-making
* Managing external tool execution

### Use Cases

Agno supports various human-in-the-loop (HITL) use cases for agents, teams and workflows:

1. **[User Confirmation](https://docs.agno.com/hitl/user-confirmation)**: Require explicit user approval before executing a tool
2. **[User Input](https://docs.agno.com/hitl/user-input)**: Gather specific information from users during execution
3. **[Dynamic User Input](https://docs.agno.com/hitl/dynamic-user-input)**: Have the agent collect user input as it needs it
4. **[External Tool Execution](https://docs.agno.com/hitl/external-execution)**: Execute tools outside of the agents control
5. **[Requires Approval or Audit](/hitl/approval)**: The workflow requires an admin review (approval/rejection) or an optional audit-only mode logging without pausing

### HITL Requirements

During Human-in-the-Loop flows, the agent run pauses until the HITL requirement are resolved by the admin, user or external tool. You can interact with the HITL requirements in the code as follows:

```python
# We run the Agent and get the run response
run_response = agent.run("Perform sensitive operation")

# In our run_response, we will find a list of active requirements:
for requirement in run_response.active_requirements:

    # We can now iterate over the requirements and resolve them:

    # For example, if the requirement needs user confirmation:
    if requirement.needs_confirmation:
        # Ask the user for confirmation
        confirmation = input(f"Do you approve the tool call to {requirement.tool.tool_name} with args {requirement.tool.tool_args}? (y/n): ")

        # Resolve the requirement by confirming or rejecting it, based on the user's input
        if confirmation.lower() == "y":
            requirement.confirm()
        else:
            requirement.reject()
```

Similarly, you can check the requirement for resolving the HITL pauses to see if a user input is required or an external tool is required:

```python
for requirement in run_response.active_requirements:

    # If the requirement is about user confirmation:
    if requirement.needs_confirmation:
        ...

    # If the requirement is about obtaining user input:
        ...

    # If the requirement is about executing an external tool:
    if requirement.is_external_tool_execution:
        ...
```

### Resuming Execution

After all active requirements have been resolved, you can continue the run by calling the `continue_run` method. The `continue_run` method continues with the state of the agent at the time of the pause.

```python
run_response = agent.run("Perform sensitive operation")

for requirement in run_response.active_requirements:
    # You handle any active requirements here

# After resolving all requirements, you can continue the run:
response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

You can also call the `continue_run` method passing the `RunOutput` of the specific run to continue:

```python
response = agent.continue_run(run_response=run_response)
```

### Streaming HITL Flows

You can also stream the responses you get during a Human-in-the-Loop flow. This is useful when you want to process or show the response in real-time. You can also stream the events resulting from calling the `continue_run` or `acontinue_run` methods. For streaming you must handle the events received while streaming the Agent run. If any event is paused, then you will need to handle the active requirements:

```python
for run_event in agent.run("Perform sensitive operation", stream=True):
    if run_event.is_paused:
        for requirement in run_event.active_requirements:
            # You handle any active requirements here

    response = agent.continue_run(run_id=run_event.run_id, requirements=run_event.requirements, stream=True)
```

### Teams

HITL works the same way for Teams. If a member agent calls a tool that requires confirmation, user input, or external execution, the team run pauses until the requirement is resolved. Each requirement includes `member_agent_name`, so you know which agent triggered it.

```python
run_response = team.run("What is the weather in Tokyo?")

if run_response.is_paused:
    for requirement in run_response.active_requirements:
        if requirement.needs_confirmation:
            print(f"Member {requirement.member_agent_name} wants to call {requirement.tool_execution.tool_name}")
            requirement.confirm()

    run_response = team.continue_run(run_response)
```

Tools attached directly to the Team (rather than individual member agents) also support HITL. If the team leader calls a tool that requires confirmation, the run pauses in the same way. See [Team HITL examples](https://docs.agno.com/examples/teams/human-in-the-loop/overview) for complete examples.

### Learn More

### Developer Resources

* [Agent HITL examples](https://docs.agno.com/examples/agents/human-in-the-loop/overview)
* [Team HITL examples](https://docs.agno.com/examples/teams/human-in-the-loop/overview)

---

## [User Confirmation](https://docs.agno.com/hitl/user-confirmation)

User confirmation allows you to pause execution and require explicit user approval before proceeding with tool calls. This is useful for:

* Sensitive operations
* API calls that modify data
* Actions with significant consequences

### How It Works

When you mark a tool with `@tool(requires_confirmation=True)`, your agent will:

1. **Pause execution** when the tool is about to be called
2. **Set `is_paused` to `True`** on the run response
3. **Wait for you** to review the tool call and decide whether to approve or reject it
4. **Continue execution** once you call `continue_run()` with your decision

This gives you complete control over which tools execute and when, making it perfect for production scenarios where you need human oversight.

### Basic Example

The following example shows how to implement user confirmation with a custom tool:

```python
from agno.tools import tool
from agno.agent import Agent
from agno.models.openai import OpenAIResponses

@tool(requires_confirmation=True)
def sensitive_operation(data: str) -> str:
    """Perform a sensitive operation that requires confirmation."""
    # Implementation here
    return "Operation completed"

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[sensitive_operation],
)

# Run the agent
run_response = agent.run("Perform sensitive operation")

# Handle confirmation
for requirement in run_response.active_requirements:
    if requirement.needs_confirmation:
        # Get user confirmation
        print(f"Tool {requirement.tool.tool_name}({requirement.tool.tool_args}) requires confirmation")
        confirmed = input(f"Confirm? (y/n): ").lower() == "y"
        requirement.confirmed = confirmed

# After resolving the requirement, you can continue the run:
response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

### Toolkit-Level Confirmation

You can also specify which specific tools in a toolkit require confirmation using the `requires_confirmation_tools` parameter. This is super useful when you want to protect only certain operations in a toolkit while allowing others to run freely:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools import tool
from agno.tools.yfinance import YFinanceTools
from agno.utils import pprint
from rich.console import Console
from rich.prompt import Prompt

console = Console()

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[YFinanceTools(requires_confirmation_tools=["get_stock_price"])],
    markdown=True,
)

run_response = agent.run("Get the current stock price of Apple?")

for requirement in run_response.active_requirements:
    if requirement.needs_confirmation:
        # Ask for confirmation
        console.print(
            f"Tool name [bold blue]{tool.tool_name}({tool.tool_args})[/] requires confirmation."
        )
        message = (
            Prompt.ask("Do you want to continue?", choices=["y", "n"], default="y")
            .strip()
            .lower()
        )

        if message == "n":
            requirement.reject()
        else:
            # Update the tools in place
            requirement.confirm()

response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
pprint.pprint_run_response(run_response)
```

### Providing Rejection Feedback

When rejecting a tool call, you can provide feedback to the agent using the `confirmation_note` property. This helps the agent understand why the operation was rejected and potentially choose a better approach:

```python
if run_response.is_paused:
    for tool in run_response.tools_requiring_confirmation:
        print(f"Tool {tool.tool_name}({tool.tool_args}) requires confirmation")
        confirmed = input(f"Confirm? (y/n): ").lower() == "y"

        if confirmed:
            tool.confirmed = True
        else:
            tool.confirmed = False
            tool.confirmation_note = "This operation was rejected because it targets the wrong resource. Please use the alternative method."

    response = agent.continue_run(run_id=run_response.run_id, updated_tools=run_response.tools)
```

### Mixed Tool Scenarios

You can mix tools that require confirmation with tools that dont. The agent will execute the non-confirmation tools automatically and only pause for those that need approval:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools import tool

def safe_operation() -> str:
    """This runs automatically without confirmation."""
    return "Safe operation completed"

@tool(requires_confirmation=True)
def risky_operation() -> str:
    """This requires user confirmation."""
    return "Risky operation completed"

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[safe_operation, risky_operation],
)

run_response = agent.run("Perform both operations")

if run_response.is_paused:
    # Only the risky_operation will be in tools_requiring_confirmation
    for tool in run_response.tools_requiring_confirmation:
        # Handle confirmation...
        tool.confirmed = True

    response = agent.continue_run(run_id=run_response.run_id, updated_tools=run_response.tools)
```

### Async Support

User confirmation works seamlessly with async agents. Just use `arun()` and `acontinue_run()`:

```python
run_response = await agent.arun("Perform sensitive operation")

if run_response.is_paused:
    for tool in run_response.tools_requiring_confirmation:
        tool.confirmed = True

    response = await agent.acontinue_run(run_response=run_response)
```

### Streaming Support

User confirmation also works with streaming responses. The agent will pause mid-stream when it encounters a tool that requires confirmation:

```python
for run_event in agent.run("Perform sensitive operation", stream=True):
    if run_event.is_paused:
        for tool in run_event.tools_requiring_confirmation:
            tool.confirmed = True

        # Continue streaming
        response = agent.continue_run(
            run_id=run_event.run_id,
            updated_tools=run_event.tools,
            stream=True
        )
```

### Usage Examples

---

## [User Input](https://docs.agno.com/hitl/user-input)

User input flows allow you to gather specific information from users during execution. This is useful for:

* Collecting required parameters
* Getting user preferences
* Gathering missing information

### How It Works

When you mark a tool with `@tool(requires_user_input=True)`, your agent will:

1. **Pause execution** before calling the tool
2. **Set `is_paused` to `True`** on the run response
3. **Populate `user_input_schema`** with the fields that need to be filled
4. **Wait for you** to provide the requested values
5. **Continue execution** once you call `continue_run()` with the filled values

The key difference from user confirmation is that here youre actually providing _data_ to fill in the tools parameters, not just approving or rejecting the tool call.

### Collecting Specific Fields

You can control which fields require user input using the `user_input_fields` parameter. Fields not in this list will be filled by the agent automatically based on the conversation context. In the example below, the agent pauses to collect the `to_address` parameter from the user for the `send_email` tool:

```python
from typing import List

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools import tool
from agno.tools.function import UserInputField
from agno.utils import pprint


# You can either specify the user_input_fields or leave empty for all fields to be provided by the user
@tool(requires_user_input=True, user_input_fields=["to_address"])
def send_email(subject: str, body: str, to_address: str) -> str:
    """
    Send an email.

    Args:
        subject (str): The subject of the email.
        body (str): The body of the email.
        to_address (str): The address to send the email to.
    """
    return f"Sent email to {to_address} with subject {subject} and body {body}"


agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[send_email],
    markdown=True,
)

run_response = agent.run(
    "Send an email with the subject 'Hello' and the body 'Hello, world!'"
)

for requirement in run_response.active_requirements:
    if requirement.needs_user_input:
        input_schema: List[UserInputField] = requirement.user_input_schema  # type: ignore

        for field in input_schema:
            # Get user input for each field in the schema
            field_type = field.field_type
            field_description = field.description

            # Display field information to the user
            print(f"\nField: {field.name}")
            print(f"Description: {field_description}")
            print(f"Type: {field_type}")

            # Get user input
            if field.value is None:
                user_value = input(f"Please enter a value for {field.name}: ")
            else:
                print(f"Value: {field.value}")
                user_value = field.value

            # Update the field value
            field.value = user_value

# After resolving the run requirements, you can continue the run
run_response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
pprint.pprint_run_response(run_response)
```

In this example, the agent will fill in `subject` and `body` based on the users request (Hello and Hello, world!), but will pause and ask the user for the `to_address` since its in the `user_input_fields` list.

### Understanding UserInputField

The `RunOutput` object has a list of requirements. When a tool requires user input, you will find a requirement object with a `user_input_schema` field, populated with `UserInputField` objects:

```python
class UserInputField:
    name: str  # The name of the field
    field_type: Type  # The required type of the field
    description: Optional[str] = None  # The description of the field
    value: Optional[Any] = None  # The value of the field. Populated by the agent or the user.
```

### Collecting All Fields

If you want the user to provide _all_ fields instead of letting the agent fill some automatically, simply omit the `user_input_fields` parameter or pass an empty list:

```python
@tool(requires_user_input=True)  # No user_input_fields means all fields need user input
def send_email(subject: str, body: str, to_address: str) -> str:
    """Send an email."""
    return f"Sent email to {to_address} with subject {subject} and body {body}"
```

This is useful when you want complete control over the data being passed to sensitive operations, or when you dont trust the LLM to extract the right values from context.

### Handling Pre-Filled Values

When you specify `user_input_fields`, youre telling the agent which parameters the user should provide. The agent will automatically fill in the other parameters based on the conversation context. For example, with `user_input_fields=["to_address"]` on a `send_email(subject, body, to_address)` function:

* **`subject` and `body`** (not in the list) Agent fills these from context, `value="Hello"` etc.
* **`to_address`** (in the list) User must provide this, `value=None`

The `user_input_schema` will include all parameters, but you only need to collect values for fields where `value=None`:

```python
# You can either specify the user_input_fields or leave empty for all fields to be provided by the user
@tool(requires_user_input=True, user_input_fields=["to_address"])
def send_email(subject: str, body: str, to_address: str) -> str:
    """
    Send an email.

    Args:
        subject (str): The subject of the email.
        body (str): The body of the email.
        to_address (str): The address to send the email to.
    """
    return f"Sent email to {to_address} with subject {subject} and body {body}"

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[send_email],
)

run_response = agent.run("Send an email with the subject 'Hello' and the body 'Hello, world!'")
for requirement in run_response.active_requirements:
    if requirement.needs_user_input:
        input_schema: List[UserInputField] = requirement.user_input_schema

        for field in input_schema:
            # Display field information to the user
            print(f"\nField: {field.name} ({field.field_type.__name__}) -> {field.description}")

            # Get user input (if the value is not set, it means the user needs to provide the value)
            if field.value is None:
                user_value = input(f"Please enter a value for {field.name}: ")
                field.value = user_value
            else:
                print(f"Value provided by the agent: {field.value}")

run_response = (
    agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
)
```

### Async Support

User input works seamlessly with async agents. Just use `arun()` and `acontinue_run()`:

```python
run_response = await agent.arun("Send an email with the subject 'Hello'")

for requirement in run_response.active_requirements:
    if requirement.needs_user_input:
        for field in requirement.user_input_schema:
            if field.value is None:
                field.value = input(f"Please enter {field.name}: ")

response = await agent.acontinue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

### Streaming Support

User input also works with streaming. The agent will emit events until it needs user input, then pause:

```python
for run_event in agent.run("Send an email", stream=True):
    if run_event.is_paused:
        for tool in run_event.tools_requiring_user_input:
            for field in tool.user_input_schema:
                if field.value is None:
                    field.value = input(f"Please enter {field.name}: ")

# Continue streaming
response = agent.continue_run(
    run_id=run_event.run_id,
    updated_tools=run_event.tools,
    stream=True
)
```

### Usage Examples

### Developer Resources

---

## [Dynamic User Input](https://docs.agno.com/hitl/dynamic-user-input)

Dynamic user input lets your agent decide when it needs information from the user and proactively request it during execution. Unlike the [User Input](https://docs.agno.com/hitl/user-input) pattern where you predefine which tools need user input, this pattern gives the agent autonomy to pause and ask for information whenever it realizes it doesnt have what it needs. This pattern is ideal when:

* **The interaction flow is unpredictable**: The agent might need different information based on context
* **You want a conversational experience**: Let the agent guide the user through a form-like interaction
* **The agent should be intelligent about what it needs**: Rather than blindly requesting predefined fields, the agent determines whats missing

### How It Works

The `UserControlFlowTools` toolkit provides your agent with a special `get_user_input` tool. When the agent realizes its missing information:

1. **Agent calls `get_user_input`** with a list of fields it needs filled
2. **Execution pauses** and requirements are added to the returned `RunOutput`
3. **`user_input_schema` populated** in the requirement, with the input schema the agent created
4. **You collect the users input** and set field values in `user_input_schema`
5. **Call `continue_run()`** to resume with the filled values
6. **Repeat if needed**: Agent may request more information based on previous responses

The key difference from other HITL patterns: the _agent_ decides what fields to request and when to request them.

```python
from typing import List

from agno.agent import Agent
from agno.tools.function import UserInputField
from agno.models.openai import OpenAIResponses
from agno.tools import tool
from agno.tools.toolkit import Toolkit
from agno.tools.user_control_flow import UserControlFlowTools
from agno.utils import pprint

# Example toolkit for handling emails
class EmailTools(Toolkit):
    def __init__(self, *args, **kwargs):
        super().__init__(
            name="EmailTools", tools=[self.send_email, self.get_emails], *args, **kwargs
        )

    def send_email(self, subject: str, body: str, to_address: str) -> str:
        """Send an email to the given address with the given subject and body.

        Args:
            subject (str): The subject of the email.
            body (str): The body of the email.
            to_address (str): The address to send the email to.
        """
        return f"Sent email to {to_address} with subject {subject} and body {body}"

    def get_emails(self, date_from: str, date_to: str) -> str:
        """Get all emails between the given dates.

        Args:
            date_from (str): The start date.
            date_to (str): The end date.
        """
        return [
            {
                "subject": "Hello",
                "body": "Hello, world!",
                "to_address": "test@test.com",
                "date": date_from,
            },
            {
                "subject": "Random other email",
                "body": "This is a random other email",
                "to_address": "john@doe.com",
                "date": date_to,
            },
        ]


agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[EmailTools(), UserControlFlowTools()],
    markdown=True,
)

run_response = agent.run("Send an email with the body 'What is the weather in Tokyo?'")

# We use a while loop to continue the running until the agent is satisfied with the user input
while run_response.is_paused:
    for requirement in run_response.active_requirements:
        if requirement.needs_user_input:
            input_schema: List[UserInputField] = requirement.user_input_schema  # type: ignore

            for field in input_schema:
                # Get user input for each field in the schema
                field_type = field.field_type  # type: ignore
                field_description = field.description  # type: ignore

                # Display field information to the user
                print(f"\nField: {field.name}")  # type: ignore
                print(f"Description: {field_description}")
                print(f"Type: {field_type}")

                # Get user input
                if field.value is None:  # type: ignore
                    user_value = input(f"Please enter a value for {field.name}: ")  # type: ignore
                else:
                    print(f"Value: {field.value}")  # type: ignore
                    user_value = field.value  # type: ignore

                # Update the field value
                field.value = user_value  # type: ignore

    run_response = agent.continue_run(
        run_id=run_response.run_id,
        requirements=run_response.requirements,
    )
    if not run_response.is_paused:
        pprint.pprint_run_response(run_response)
        break
```

In this example, the agent identifies that its missing the email subject and recipient address, so it proactively calls `get_user_input` to collect that information. Pretty smart!

### Understanding the `get_user_input` Tool

When your agent calls the `get_user_input` tool, it provides a list of fields using this format:

```python
{
    "field_name": "subject",      # The field identifier
    "field_type": "str",           # Python type (str, int, float, bool, list, dict, etc.)
    "field_description": "The subject of the email"  # Helpful description for the user
}
```

The agent constructs these fields intelligently based on what it needs. For example, if its trying to send an email but doesnt have the recipient, it might request:

```python
[
    {"field_name": "to_address", "field_type": "str", "field_description": "The email address to send to"},
    {"field_name": "subject", "field_type": "str", "field_description": "The subject line for the email"}
]
```

These fields then appear in `tool.user_input_schema` as `UserInputField` objects that you can iterate through and fill. For a detailed breakdown of the `UserInputField` structure, see [Understanding UserInputField](https://docs.agno.com/hitl/user-input#understanding-userinputfield).

### The While Loop Pattern

Notice the `while run_response.is_paused:` loop? This is crucial for dynamic user input, because the agent might request input multiple times:

```python
run_response = agent.run("Send an email and schedule a meeting")

# First iteration: Agent needs email details
while run_response.is_paused:
    for requirement in run_response.requirements:
        if requirement.needs_user_input:
            for field in requirement.user_input_schema:
                if field.value is None:
                    field.value = input(f"Enter {field.name}: ")

    run_response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
    # Agent might pause again if it needs meeting details!
```

The agent could:

1. First ask for email details
2. Send the email
3. Realize it needs meeting details
4. Pause again to request those fields
5. Complete the task

This multi-round capability makes the pattern extremely flexible.

### Customizing Toolkit Behavior

The `UserControlFlowTools` toolkit comes with default instructions that guide the agent, but you can customize them:

```python
from agno.tools.user_control_flow import UserControlFlowTools

# Custom instructions for your use case
custom_instructions = """
When you need user input:
1. Only request fields you absolutely need
2. Group related fields together
3. Provide clear, concise descriptions
4. Never request the same information twice
"""

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[
        EmailTools(),
        UserControlFlowTools(
            instructions=custom_instructions,
            add_instructions=True
        )
    ],
    markdown=True,
)
```

You can also disable the tool entirely if needed:

```python
UserControlFlowTools(enable_get_user_input=False)
```

### Handling Pre-Filled Values

The agent can pre-fill some fields based on the conversation context. This works the same way as in [User Input](https://docs.agno.com/hitl/user-input#handling-pre-filled-values)always check `field.value` before prompting:

```python
for field in tool.user_input_schema:
    if field.value is None:
        user_value = input(f"Please enter {field.name}: ")
        field.value = user_value
    else:
        print(f"{field.name} (provided by agent): {field.value}")
```

For a more detailed explanation of how pre-filled values work, see the [Handling Pre-Filled Values](https://docs.agno.com/hitl/user-input#handling-pre-filled-values) section in the User Input documentation.

### Best Practices

1. **Always use a while loop**: The agent may need multiple rounds of input
2. **Check field values**: Dont overwrite fields the agent has already filled
3. **Provide clear prompts**: Use the `field.description` to help users understand whats needed
4. **Validate input**: Add your own validation before setting `field.value`
5. **Handle interruptions gracefully**: Store `run_id` to resume later if needed

### Async Support

Dynamic user input works seamlessly with async agents. Use `arun()` and `acontinue_run()` for asynchronous flows:

```python
from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.user_control_flow import UserControlFlowTools

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[EmailTools(), UserControlFlowTools()],
    markdown=True,
)

run_response = await agent.arun("Send an email")

while run_response.is_paused:
    for requirement in run_response.active_requirements:
        if requirement.needs_user_input:
            for field in requirement.user_input_schema:
                if field.value is None:
                    field.value = input(f"Please enter {field.name}: ")

    run_response = await agent.acontinue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

### Streaming Support

Dynamic user input also works with streaming. The agent will emit events until it needs user input, then pause:

```python
run_response = agent.run("Send an email", stream=True)

for run_event in run_response:
    if run_event.is_paused:
        for requirement in run_event.active_requirements:
            if requirement.needs_user_input:
                for field in requirement.user_input_schema:
                    if field.value is None:
                        field.value = input(f"Please enter {field.name}: ")

        # Continue streaming
        for continued_event in agent.continue_run(
            run_id=run_event.run_id,
            requirements=run_event.requirements,
            stream=True
        ):
            print(continued_event.content)
```

### When to Use This Pattern

**Use Dynamic User Input when:**

* The agent needs to adapt its questions based on previous responses
* You want the agent to intelligently determine what information is missing
* The interaction flow changes based on context

**Use [User Input](https://docs.agno.com/hitl/user-input) when:**

* You know exactly which tool fields require user input upfront
* The input requirements are always the same
* You want more explicit control over what gets asked

### Usage Examples

### Developer Resources

---

## [External Execution](https://docs.agno.com/hitl/external-execution)

External tool execution gives you complete control over when and how certain tools actually run. Instead of letting the agent execute the tool directly, it pauses and waits for you to handle the execution yourself. This is incredibly useful when you need:

* **Enhanced security**: Execute sensitive operations in a controlled environment
* **External service calls**: Integrate with services that require special handling
* **Database operations**: Run queries through your own connection management
* **Custom execution logic**: Add validation, logging, or rate limiting before execution
* **Sandboxed environments**: Execute potentially dangerous operations safely

### How It Works

When you mark a tool with `@tool(external_execution=True)`, your agent will:

1. **Pause execution** when the tool is about to be called
2. **Set `is_paused` to `True`** on the run response
3. **Populate `tools_awaiting_external_execution`** with tools that need external handling
4. **Wait for you** to execute the tool and set its result
5. **Continue execution** once you call `continue_run()` with the result

The key difference from other HITL patterns is that the agent never actually calls the functionyoure responsible for the entire execution.

```python
import subprocess

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools import tool
from agno.utils import pprint


# Create a tool with the correct name, arguments and docstring for the agent to know what to call.
@tool(external_execution=True)
def execute_shell_command(command: str) -> str:
    """Execute a shell command.

    Args:
        command (str): The shell command to execute

    Returns:
        str: The output of the shell command
    """
    return subprocess.check_output(command, shell=True).decode("utf-8")


agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[execute_shell_command],
    markdown=True,
)

run_response = agent.run("What files do I have in my current directory?")

for requirement in run_response.active_requirements:
    if requirement.is_external_tool_execution:
        if requirement.tool_execution.tool_name == execute_shell_command.name:
            print(f"Executing {requirement.tool_execution.tool_name} with args {requirement.tool_execution.tool_args} externally")

            # Execute the tool manually. You can execute any function or process here and use the tool_args as input.
            result = execute_shell_command.entrypoint(**requirement.tool_execution.tool_args)

            # Set the result on the tool execution object so that the agent can continue
            requirement.external_execution_result = result

run_response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
pprint.pprint_run_response(run_response)
```

In this example, the agent identifies that it needs to run `execute_shell_command` but doesnt actually execute it. Instead, it pauses and gives you the tool name and arguments. You then execute it yourself (or something completely different!) and provide the result back.

### Understanding External Tool Execution Requirements

When a run is paused for external execution, the returned `RunOutput` will contain a list of requirement objects. These requirement objects will contain the tool executions that need to run outside of the agents run. You can find the tool related to each requirement in `requirement.tool_execution`. Each tool execution object contains:

* **`tool_name`**: The name of the tool that was called
* **`tool_args`**: A dictionary of arguments the agent wants to pass to the tool
* **`external_execution_required`**: A boolean flag set to `True`
* **`result`**: Where you set the execution result (initially `None`)

You can iterate through these requirements, execute the tools however you want, and set their results:

```python
for requirement in run_response.active_requirements:
    if requirement.is_external_tool_execution:
        print(f"Tool: {requirement.tool_execution.tool_name}")
        print(f"Args: {requirement.tool_execution.tool_args}")

        # Execute your custom logic here
        result = my_custom_execution(requirement.tool_execution.tool_args)

        # Set the result so the agent can continue
        requirement.external_execution_result = result

# After resolving the requirement, you can continue the run:
response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

### Using Toolkits with External Execution

If youre using a `Toolkit`, you can specify which tools require external execution using the `external_execution_required_tools` parameter:

```python
from agno.tools.toolkit import Toolkit
import subprocess

class ShellTools(Toolkit):
    def __init__(self, *args, **kwargs):
        super().__init__(
            tools=[self.list_dir, self.get_env],
            external_execution_required_tools=["list_dir"],  # Only this one needs external execution
            *args,
            **kwargs,
        )

    def list_dir(self, directory: str):
        """Lists the contents of a directory."""
        return subprocess.check_output(f"ls {directory}", shell=True).decode("utf-8")

    def get_env(self, var_name: str):
        """Gets an environment variable."""
        import os
        return os.getenv(var_name, "Not found")

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[ShellTools()],
    markdown=True,
)

run_response = agent.run("What files are in my current directory and what's my PATH?")

for requirement in run_response.active_requirements:
    if requirement.is_external_tool_execution:
        # Only list_dir will be here, get_env runs normally
        if requirement.tool_execution.tool_name == "list_dir":
            result = ShellTools().list_dir(**requirement.tool_execution.tool_args)
            requirement.external_execution_result = result

# After resolving the requirement, you can continue the run:
response = agent.continue_run(run_id=run_response.run_id, requirements=run_response.requirements)
```

This lets you mix external and internal tools in the same toolkitperfect when you only need special handling for specific operations.

### Mixed Tool Scenarios

You can absolutely have a mix of regular tools and external execution tools in the same agent. When the agent wants to call multiple tools, only the ones marked with `@tool(external_execution=True)` will cause a pause:

```python
@tool(external_execution=True)
def sensitive_database_query(query: str) -> str:
    """Execute a database query."""
    pass

@tool
def safe_calculation(x: int, y: int) -> int:
    """Perform a safe calculation."""
    return x + y

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[sensitive_database_query, safe_calculation],
    markdown=True,
)

response = agent.run("Calculate 5 + 10 and query the users table")

# Agent will pause when it tries to call sensitive_database_query
# but safe_calculation executes normally

for requirement in response.active_requirements:
    if requirement.is_external_tool_execution:
    if requirement.tool_execution.tool_name == "sensitive_database_query":
        # Execute with your own DB connection and security checks
        result = execute_safe_db_query(requirement.tool_execution.tool_args["query"])
        requirement.external_execution_result = result

# After resolving the requirement, you can continue the run:
response = agent.continue_run(run_id=response.run_id, requirements=response.requirements)
```

### Async Support

External execution works seamlessly with async operations. Use `arun()` and `acontinue_run()` for async flows:

```python
import asyncio

@tool(external_execution=True)
async def async_external_tool(data: str) -> str:
    """An async tool requiring external execution."""
    pass

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[async_external_tool],
    markdown=True,
)

async def main():
    run_response = await agent.arun("Process some data")

    for requirement in run_response.active_requirements:
        if requirement.is_external_tool_execution:
            # Execute your async external logic
            result = await my_async_external_service(requirement.tool_execution.tool_args)
            requirement.external_execution_result = result

    response = await agent.acontinue_run(run_id=run_response.run_id, requirements=run_response.requirements)
    print(response.content)

asyncio.run(main())
```

### Streaming Support

You can also use external execution with streaming responses:

```python
for run_event in agent.run("What files are in my directory?", stream=True):
    if run_event.is_paused:
        for requirement in run_event.active_requirements:
            if requirement.is_external_tool_execution:
                # Execute externally
                result = execute_tool_externally(requirement.tool_execution.tool_args)
                requirement.external_execution_result = result

        # Continue streaming
        for response in agent.continue_run(
            run_id=run_event.run_id,
            requirements=run_event.requirements,
            stream=True
        ):
            print(response.content, end="")
    else:
        print(run_event.content, end="")
```

### Best Practices

1. **Always set results**: Make sure you set `requirement.external_execution_result` for all requirements before continuing
2. **Error handling**: Wrap your external execution in try-catch blocks and provide meaningful error messages as results
3. **Security validation**: Use external execution to add extra security checks before running sensitive operations
4. **Logging**: Log all external executions for audit trails
5. **Timeouts**: Consider adding timeouts to your external execution logic to prevent hanging

### Usage Examples

### Developer Resources

---

## [Approval](https://docs.agno.com/hitl/approval)

Approval enables a User Triggers, Admin Authorizes workflow. When an agent (or team member) hits a protected tool during a run, the run pauses and persists a pending record to your database. Execution only resumes once an admin approves or rejects the request. Approvals are built on HITL primitives (`requires_confirmation`, `requires_user_input`, or `external_execution`). Your tool must implement at least one.

### Quick start

```python
from agno.approval import approval
from agno.tools import tool
from agno.db.sqlite import SqliteDb
from agno.agent import Agent

@approval
@tool(requires_confirmation=True)
def delete_user_data(user_id: str) -> str:
    """Permanently delete all data for a user. Requires admin approval."""
    return f"All data for user {user_id} has been deleted."

db = SqliteDb(db_file="app.db", approvals_table="approvals")
agent = Agent(model=..., tools=[delete_user_data], db=db)
```

When the user asks for something that uses this tool, the run pauses and a **pending** approval is written to the database. An admin resolves it; then you continue the run.

### Approval Types

| Type | Behavior | Use Case |
| --- | --- | --- |
| `@approval(type="required") or` `@approval` | **Blocking:** Run pauses until an admin reviews and resolves the database record. | Critical actions such as deletion, payments, bulk emails. |
| `@approval(type="audit")` | **Non-blocking:** Run continues immediately after the HITL interaction is resolved and an audit log is created. | Compliance and activity auditing purposes. |

### Blocking

By default, `@approval` needs HITL approval and `requires_confirmation=True` is set.

### Non-blocking

To enable an audit-style (non-blocking) Human-in-the-Loop flow for persistent audit trails, use `@approval(type="audit")`. This will create an audit log after the HITL interaction is resolved. You can use the `@tool` decorator with `log_approval=True` to explicitly signal that this tools execution should be logged in the HITL audit system. See [User Confirmation](https://docs.agno.com/hitl/user-confirmation) for details.

### Execution Flow

There are three distinct phases in the approval flow:

* **The Pause:** When a user triggers an `@approval` tool, the SDK automatically pauses the run and inserts a pending record into your database.
* **Admin Approval:** Admin views the list of pending requests. Then update the record status via the DB provider. Use `expected_status="pending"` to prevent race conditions.

```python
db.update_approval(
    approval_id,
    expected_status="pending",
    status="approved",   # or "rejected"
    resolved_by="admin_user_id",
    resolved_at=int(time.time()),
    # For requires_user_input or external_execution: pass resolution_data
    # (e.g. values for user input, result for external execution); SDK applies it on continue_run.
)
```

* **Resuming the Run:** Continue the run using the `run_id`. The SDK verifies the resolution before proceeding. If the record is missing or still pending, `continue_run` raises a `RuntimeError`.

```python
run = agent.continue_run(run_id=run.run_id, requirements=run.requirements)
```

### Examples

### Developer Resources

* Example code: [Approvals cookbook](https://github.com/agno-agi/agno/blob/main/cookbook/02_agents/11_approvals)
* [Approval reference](https://docs.agno.com/reference-api/schema/approvals/list-approvals)
* [Tool decorator](https://docs.agno.com/reference/tools/decorator)

---

If you want, I can also turn this into:
- one clean `.md` file,
- a section-by-section summarized version,
- or a comparison table for all HITL patterns.