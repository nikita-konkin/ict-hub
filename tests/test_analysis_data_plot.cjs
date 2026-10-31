const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/js/analysis-data-plot.js'), 'utf8'), context);
const adapter = context.window.AnalysisDataPlot;
const plain = (value) => JSON.parse(JSON.stringify(value));

test('column-oriented measurements keep nulls aligned with time', () => {
  const rows = adapter.flattenRows({ station: 'mork', series: { ut: [0, 1, 2], tec: [20, null, 25] } });
  assert.deepEqual(plain(rows).map(({ ut, tec, station }) => ({ ut, tec, station })), [
    { ut: 0, tec: 20, station: 'mork' }, { ut: 1, tec: null, station: 'mork' }, { ut: 2, tec: 25, station: 'mork' },
  ]);
  const figure = adapter.buildFigure(rows, 'ut', ['tec'], 'TEC');
  assert.deepEqual(plain(figure.data[0].y), [20, null, 25]);
  assert.equal(figure.data[0].connectgaps, false);
});

test('nested station/day/satellite results produce separate traces', () => {
  const rows = adapter.flattenRows({ data: [
    { station: 'mork', doy: 122, rows: [{ satellite: 'G01', ut: 0, tec: '21' }, { satellite: 'G02', ut: 0, tec: 22 }] },
    { station: 'ntor', doy: 123, rows: [{ satellite: 'G01', ut: 0, tec: 23 }] },
  ], meta: { count: 3 } });
  assert.equal(rows.length, 3);
  const figure = adapter.buildFigure(rows, 'ut', ['tec'], 'TEC');
  assert.equal(figure.data.length, 3);
  assert.deepEqual(plain(figure.data.map((trace) => trace.y[0])), [21, 22, 23]);
  assert.match(figure.data[0].name, /station=mork/);
});

test('null, blank and boolean values are not manufactured zero measurements', () => {
  const rows = [{ x: 1, y: null, valid: true }, { x: 2, y: '', valid: false }, { x: 3, y: 0 }];
  assert.deepEqual(plain(adapter.numericColumns(rows)), ['x', 'y']);
  assert.deepEqual(plain(adapter.buildFigure(rows, 'x', ['y'], '').data[0].y), [null, null, 0]);
});

test('metadata lists can be visualized as counts; empty results stay empty', () => {
  const rows = adapter.flattenRows({ stations: ['mork', 'ntor', 'mork'] });
  const figure = adapter.buildFigure(rows, 'stations', ['__count__'], 'Stations');
  assert.deepEqual(plain(figure.data[0].x), ['mork', 'ntor']);
  assert.deepEqual(plain(figure.data[0].y), [2, 1]);
  assert.deepEqual(plain(adapter.buildFigure([], '', ['__count__'], '').data), []);
});
