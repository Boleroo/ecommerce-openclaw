import { executeDispatcherTool } from "../openclaw-plugin/dist/adapter.js";

const [tool, encodedArguments] = process.argv.slice(2);
if (!tool || !encodedArguments) throw new Error("tool and JSON arguments are required");
const result = await executeDispatcherTool(tool, JSON.parse(encodedArguments));
process.stdout.write(JSON.stringify(result));
