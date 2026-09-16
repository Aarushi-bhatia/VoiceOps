/** Starting point for a brand-new agent: valid, minimal, and obviously editable. */
export const STARTER_WORKFLOW = {
  start_node: 'greeting',
  nodes: [
    {
      id: 'greeting',
      type: 'branch',
      label: 'Greeting',
      prompt: 'Hi, thanks for calling. What can I help you with today?',
      reprompt: "Sorry, I didn't catch that. What can I help you with?",
      intents: [
        {
          name: 'support',
          description: 'has a problem with an order or product',
          examples: ['my order is wrong', 'it arrived broken'],
          next: 'goodbye',
        },
      ],
      default: 'handover',
      max_attempts: 2,
    },
    {
      id: 'goodbye',
      type: 'hangup',
      label: 'Close the call',
      prompt: "Thanks — I've logged that and someone will follow up shortly.",
      outcome: 'resolved',
    },
    {
      id: 'handover',
      type: 'transfer',
      label: 'Transfer to a human',
      prompt: "Let me put you through to a colleague who can help.",
      destination: 'support-queue',
      outcome: 'escalated',
    },
  ],
}

export const NODE_TYPES = [
  { value: 'say', label: 'Say', help: 'Speak a line, then continue.' },
  { value: 'collect', label: 'Collect', help: 'Ask for a value and capture it.' },
  { value: 'branch', label: 'Branch', help: 'Ask a question and route on the answer.' },
  { value: 'transfer', label: 'Transfer', help: 'Hand the call to a human queue.' },
  { value: 'hangup', label: 'Hang up', help: 'End the call with an outcome.' },
]

export const FIELD_TYPES = ['text', 'order_id', 'email', 'phone', 'number', 'date', 'zip', 'boolean']
export const OUTCOMES = ['resolved', 'escalated', 'callback_scheduled', 'unresolved']
