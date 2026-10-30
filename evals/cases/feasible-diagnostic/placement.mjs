// Reduced placement lifecycle: callers may replace a panel while measuring it.
export class Placement {
  constructor() { this.current = null; this.scroll = 0; this.trace = []; }
  replace(id) {
    this.current = { id, cancelled: false };
    this.trace.push({ event: 'replace', id });
    return this.current;
  }
  cancel(operation) {
    operation.cancelled = true;
    this.trace.push({ event: 'cancel', id: operation.id });
  }
  async place(operation, measure) {
    if (operation.cancelled || operation !== this.current) return;
    const target = await measure();
    this.scroll = target;
    this.trace.push({ event: 'write', id: operation.id, target });
  }
}
