/** Require literal review content and an actual recorded SDK command.
 * This checks minimum evidence presence; the model judge assesses whether the
 * commands investigate the relevant code and support the review decision.
 */
module.exports = function reviewEvidence(output) {
  let observation;
  try {
    observation = JSON.parse(output);
  } catch {
    return { pass: false, score: 0, reason: "Trajectory output is not valid JSON" };
  }
  const pass = observation !== null &&
    typeof observation === "object" &&
    typeof observation.artifact === "string" &&
    observation.artifact.trim().length > 0 &&
    Array.isArray(observation.items) &&
    observation.items.some((item) => item !== null &&
      typeof item === "object" &&
      item.type === "command_execution" &&
      typeof item.command === "string" &&
      item.command.trim().length > 0);
  return {
    pass,
    score: pass ? 1 : 0,
    reason: pass ? "Review artifact and recorded command present" :
      "Review requires a nonblank artifact and recorded command execution",
  };
};
