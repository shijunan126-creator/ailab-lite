// The viewer imports one helper from @modelcontextprotocol/ext-apps; the Portal passes no host style variables.
export function applyHostStyleVariables(variables: Record<string, string | undefined>) {
  for (const [name, value] of Object.entries(variables)) if (value) document.documentElement.style.setProperty(name, value);
}
