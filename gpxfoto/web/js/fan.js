// Where photos taken at one place go when they are fanned out around it.

const FAN_STEP = 44;                 // px between the centres of fanned-out photos
const FAN_CIRCLE = 8;                // up to this many photos on a circle, more on a spiral

// Offsets in px of n photos from their place: on a circle, or for many of
// them on a spiral, each about FAN_STEP or more from the others
export function fanOffsets(n) {
  const offsets = [];
  if (n <= FAN_CIRCLE) {
    // Neighbours on the circle are a chord apart
    const radius = n < 2 ? FAN_STEP : Math.max(FAN_STEP, FAN_STEP / (2 * Math.sin(Math.PI / n)));
    for (let k = 0; k < n; k++) {
      const angle = -Math.PI / 2 + (2 * Math.PI * k) / n;
      offsets.push([radius * Math.cos(angle), radius * Math.sin(angle)]);
    }
    return offsets;
  }
  // An Archimedean spiral whose turns are FAN_STEP apart
  const growth = FAN_STEP / (2 * Math.PI);
  let angle = FAN_STEP / growth;
  for (let k = 0; k < n; k++) {
    const radius = growth * angle;
    offsets.push([radius * Math.cos(angle), radius * Math.sin(angle)]);
    angle += FAN_STEP / radius;
  }
  return offsets;
}
