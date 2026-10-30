import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Placement } from './placement.mjs';

test('ordinary placement', async () => {
  const view = new Placement();
  const operation = view.replace('first');
  await view.place(operation, () => 100);
  assert.equal(view.scroll, 100);
});
test('cancellation before placement', async () => {
  const view = new Placement();
  const operation = view.replace('first');
  view.cancel(operation);
  await view.place(operation, () => 100);
  assert.equal(view.scroll, 0);
});
test('completed placement followed by replacement', async () => {
  const view = new Placement();
  await view.place(view.replace('first'), () => 100);
  await view.place(view.replace('second'), () => 260);
  assert.equal(view.scroll, 260);
});
