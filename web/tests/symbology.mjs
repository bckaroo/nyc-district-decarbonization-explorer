// DEV-157: use MapLibre's actual evaluator, not a reimplementation.
// Run from web/: node --experimental-strip-types tests/symbology.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createExpression, v8} from '@maplibre/maplibre-gl-style-spec';
import {OBSERVED_THEMES, UNAVAILABLE_THEMES, colorExpr, opacityExpr, getTheme, legendSwatches} from '../src/symbology.ts';
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
test('annual thermal end uses remain unavailable until modeled', () => {
  for (const id of ['heating_demand','cooling_demand','dhw_demand']) {
    const t=UNAVAILABLE_THEMES.find(x=>x.id===id);
    assert.ok(t);
    assert.match(t.reason,/model/i);
    assert.ok(!OBSERVED_THEMES.some(x=>x.id===id));
  }
});
console.log(`Symbology tests: ${passed} passing; ${fc.features.length} features checked per theme.`);
