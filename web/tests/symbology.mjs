// DEV-157: use MapLibre's actual evaluator, not a reimplementation.
// Run from web/: node --experimental-strip-types tests/symbology.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createExpression, v8} from '@maplibre/maplibre-gl-style-spec';
import {OBSERVED_THEMES, UNAVAILABLE_THEMES, MODELED_THEMES, colorExpr, opacityExpr, getTheme, legendSwatches} from '../src/symbology.ts';
const fc = JSON.parse(readFileSync(new URL('../../data/snapshots/footprints_joined.geojson', import.meta.url), 'utf8'));
let passed = 0;
function test(name, fn) { fn(); passed++; console.log(`ok ${name}`); }
function compile(expr, property) {
  const parsed = createExpression(expr, `layers[0].paint.${property}`, v8.paint_fill[property]);
  assert.equal(parsed.result, 'success', JSON.stringify(parsed.value));
  return (properties) => parsed.value.evaluate({zoom: 0}, {type: 'Polygon', properties});
}
function rgb(hex) { return [1,3,5].map(i => parseInt(hex.slice(i, i+2),16)/255); }
function closeColor(actual, expected) {
  const values = Array.isArray(expected) ? expected : rgb(expected);
  for (const [i, channel] of ['r','g','b'].entries()) assert.ok(Math.abs(actual[channel]-values[i])<1e-6, `${channel}: ${actual[channel]} != ${values[i]}`);
  assert.equal(actual.a, 1);
}
for (const theme of OBSERVED_THEMES) {
  const color = compile(colorExpr(theme), 'fill-color');
  const opacity = compile(opacityExpr(theme, null), 'fill-opacity');
  test(`${theme.id}: schema and legend`, () => {
    assert.ok(fc.features.some(f => Object.hasOwn(f.properties, theme.field)));
    assert.equal(theme.ramp.length, theme.breaks.length+1);
    assert.equal(legendSwatches(theme).length, theme.ramp.length);
    theme.breaks.forEach((b,i) => assert.ok(Number.isFinite(b) && b > (i ? theme.breaks[i-1] : 0)));
    assert.ok(theme.units);
  });
  test(`${theme.id}: exact continuous color stops`, () => {
    [0,...theme.breaks].forEach((value,i) => closeColor(color({[theme.field]:value}),theme.ramp[i]));
    closeColor(color({[theme.field]: theme.breaks.at(-1)*2}),theme.ramp.at(-1));
  });
  test(`${theme.id}: missing and nonnumeric values stay gray`, () => {
    for (const p of [{},{[theme.field]:null},{[theme.field]:'N/A'}]) {
      closeColor(color(p),theme.nullGray);
      assert.equal(opacity(p),0.16);
    }
  });
  test(`${theme.id}: full snapshot evaluates`, () => {
    for (const f of fc.features) {
      const c = color(f.properties);
      for (const key of ['r','g','b','a']) assert.ok(Number.isFinite(c[key]));
      assert.ok(opacity(f.properties)>0);
    }
  });
  test(`${theme.id}: reporting boundary disclosure`, () => {
    assert.match(theme.grain,/property/i);
    if (theme.disaggregated) {
      assert.match(theme.grain,/estimated/i);
      assert.match(theme.grain,/area-weight/i);
    }
    assert.doesNotMatch(theme.label,/heating|cooling demand/i);
  });
}
test('EUI midpoint is linearly interpolated, not a class boundary', () => {
  const t=getTheme('site_eui_kbtu_ft');
  const fraction=(60-t.breaks[0])/(t.breaks[1]-t.breaks[0]);
  const low=rgb(t.ramp[1]), high=rgb(t.ramp[2]);
  closeColor(compile(colorExpr(t),'fill-color')({[t.field]:60}),low.map((v,i)=>v+(high[i]-v)*fraction));
});
test('selection opacity overrides missing data', () => {
  const t=OBSERVED_THEMES[0];
  assert.equal(compile(opacityExpr(t,'selected'),'fill-opacity')({pid:'selected'}),0.92);
  assert.equal(compile(opacityExpr(t,null),'fill-opacity')({[t.field]:50}),0.55);
});
test('unsuffixed electricity field is kBtu, not kWh', () => {
  const t=getTheme('electricity_use_grid_purchase');
  assert.equal(t.field,'electricity_use_grid_purchase');
  assert.equal(t.units,'kBtu/yr');
});
test('property GFA is not allocated by the current data pipeline', () => {
  assert.equal(getTheme('property_gfa_self_reported').disaggregated,false);
});
test('modeled thermal themes are enabled with honest modeled labels', () => {
  for (const id of ['heating_demand','cooling_demand','dhw_demand','net_thermal_kbtu_ft2_yr']) {
    const t = MODELED_THEMES.find(x=>x.id===id);
    assert.ok(t, `${id} must be a MODELED theme`);
    assert.match(t.grain,/modeled\/estimated/i);
    assert.match(t.grain,/not measured/i);
    assert.match(t.grain,/evidence_tier/i);
    assert.match(t.grain,/η=0\.8|eta=0\.8/);
    assert.ok(!OBSERVED_THEMES.some(x=>x.id===id));
    assert.ok(!UNAVAILABLE_THEMES.some(x=>x.id===id));
  }
});
// Modeled features come from the merged footprints_joined_demand.geojson.
const fcModeled = JSON.parse(readFileSync(new URL('../../data/snapshots/footprints_joined_demand.geojson', import.meta.url), 'utf8'));

for (const theme of MODELED_THEMES) {
  const determining = (props) => props[theme.decidingField];
  const color = compile(colorExpr({...theme, field: theme.decidingField}), 'fill-color');
  const opacity = compile(opacityExpr(theme, null), 'fill-opacity');
  test(`${theme.id} (modeled): schema and honest labeling`, () => {
    assert.ok(fcModeled.features.some(f => Object.hasOwn(f.properties, theme.decidingField)));
    // Ramp structure: rampStart (or 0) + one stop per break, plus a tail stop.
    assert.ok(theme.ramp.length === theme.breaks.length + 1 || theme.ramp.length === theme.breaks.length + 2);
    assert.equal(legendSwatches(theme).length, theme.ramp.length);
    theme.breaks.forEach((b,i) => assert.ok(Number.isFinite(b) && b > (i ? theme.breaks[i-1] : theme.breaks[0] < 0 ? -Infinity : 0)));
    assert.match(theme.grain,/modeled\/estimated/i);
    assert.match(theme.grain,/not measured/i);
    assert.match(theme.grain,/evidence_tier/i);
  });
  if (theme.id === 'net_thermal_kbtu_ft2_yr') {
    test('net_thermal: diverging ramp — near zero is neutral, hot/cold map correctly', () => {
      // Neutral at 0 — which in the diverging theme is break[2] (the "+20" stop
      // is actually ±0-crossing white): 0 interpolates INTO the white bucket.
      const c0 = color({'net_thermal_kbtu_ft2_yr':0});
      assert.ok(c0.r > 0.7 && c0.g > 0.7 && c0.b > 0.7, '0 is light/neutral, not saturated');
      // Positive (net heating) → red side; negative (net cooling) → blue side.
      const plus = color({'net_thermal_kbtu_ft2_yr':200});
      const minus = color({'net_thermal_kbtu_ft2_yr':-200});
      assert.ok(plus.r > plus.b, '+200 is red-dominant');
      assert.ok(minus.b > minus.r, '−200 is blue-dominant');
      // Just above lower edge of neutral band is NOT fully saturated either way.
      const c5 = color({'net_thermal_kbtu_ft2_yr':5});
      assert.ok(c5.g > c5.r, '+5 should lean blue, not red');
    });
  }
  test(`${theme.id} (modeled): missing and nonnumeric values stay gray`, () => {
    for (const p of [{},{[theme.decidingField]:null},{[theme.decidingField]:'N/A'}]) {
      closeColor(color(p),theme.nullGray);
      assert.equal(opacity(p),0.16);
    }
  });
  test(`${theme.id} (modeled): full merged snapshot evaluates`, () => {
    for (const f of fcModeled.features) {
      const c = color(f.properties);
      for (const key of ['r','g','b','a']) assert.ok(Number.isFinite(c[key]));
      assert.ok(opacity(f.properties)>0);
    }
  });
}
console.log(`Symbology tests: ${passed} passing; ${fc.features.length} features checked per theme.`);
