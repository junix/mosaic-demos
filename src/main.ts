import * as duckdb from '@duckdb/duckdb-wasm';
import {Coordinator, DuckDBWASMConnector, createAPIContext} from '@uwdata/vgplot';
import mvpWasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url';
import ehWasm from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url';
import mvpWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url';
import ehWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url';
import './style.css';

type SceneName = 'density' | 'signals' | 'linked' | 'distribution' | 'regression' | 'retention' | 'quality-matrix' | 'rankings' | 'anomalies' | 'vector-field' | 'small-multiples' | 'operations';

const scenes: Record<SceneName, {title: string; subtitle: string; rows: number}> = {
  density: {
    title: 'A million points, queried as pixels',
    subtitle: 'DuckDB-WASM bins a deterministic point cloud; Mosaic asks only for the screen-level density needed by the plot.',
    rows: 1_000_000
  },
  signals: {
    title: 'Dense temporal signal atlas',
    subtitle: 'Four long time series are compressed into a legible overview with database-backed aggregation and pan/zoom.',
    rows: 400_000
  },
  linked: {
    title: 'Linked operational rhythms',
    subtitle: 'Brush the scatter field to filter the hourly profile; both views are coordinated through a shared SQL selection.',
    rows: 320_000
  },
  distribution:{title:'Database-backed distributions',subtitle:'Four dense cohorts remain queryable while their shapes are compared directly.',rows:360_000},
  regression:{title:'Dense relationship explorer',subtitle:'Hexagonal density and a fitted trend expose structure without overplotting.',rows:480_000},
  retention:{title:'Cohort retention atlas',subtitle:'Eight cohorts are aggregated by period inside DuckDB before rendering.',rows:320_000},
  'quality-matrix':{title:'Quality risk matrix',subtitle:'Subsystem and lifecycle phase form a database-aggregated risk surface.',rows:280_000},
  rankings:{title:'Regional performance ranks',subtitle:'DuckDB aggregates each region into a ranked mean, a 10th–90th percentile interval bar, and its sample count.',rows:240_000},
  anomalies:{title:'Rare-event signal monitor',subtitle:'Dense temporal values and exceptional spikes share one scalable view.',rows:420_000},
  'vector-field':{title:'Sampled flow vectors',subtitle:'A SQL-generated directional field is rendered as magnitude-aware glyphs.',rows:180_000},
  'small-multiples':{title:'Service behavior small multiples',subtitle:'Four coordinated views compare latency, load, errors, and saturation.',rows:360_000},
  operations:{title:'Linked operations cockpit',subtitle:'A database-backed analytical dashboard aligns load, latency, and incidents.',rows:520_000}
};

const params = new URLSearchParams(location.search);
const requested = params.get('scene');
const scene: SceneName = requested && requested in scenes ? requested as SceneName : 'density';
const exportMode = params.get('export') === '1';
const meta = scenes[scene];
const runtime: NonNullable<Window['__mosaicDemo']> = window.__mosaicDemo = {ready: false, scene, rows: meta.rows, interactions: 0};

document.querySelector<HTMLElement>('#app')!.innerHTML = `
  <section class="shell ${exportMode ? 'export' : ''}">
    <header class="header">
      <div>
        <div class="kicker">Mosaic · DuckDB-WASM · vgplot</div>
        <h1>${meta.title}</h1>
        <p class="subtitle">${meta.subtitle}</p>
        <nav class="nav">
          ${Object.keys(scenes).map(key => `<a class="${key === scene ? 'active' : ''}" href="?scene=${key}">${key}</a>`).join('')}
        </nav>
      </div>
      <div class="badge">${meta.rows.toLocaleString()} LOCAL ROWS</div>
    </header>
    <section class="figure">
      <div class="chart" id="chart"><div class="loading">Starting local analytical engine…</div></div>
      <div class="status" id="status">initializing</div>
    </section>
  </section>`;

async function localDatabase(): Promise<duckdb.AsyncDuckDB> {
  const bundles: duckdb.DuckDBBundles = {
    mvp: {mainModule: mvpWasm, mainWorker: mvpWorker},
    eh: {mainModule: ehWasm, mainWorker: ehWorker}
  };
  const bundle = await duckdb.selectBundle(bundles);
  if (!bundle.mainWorker) throw new Error('DuckDB-WASM worker bundle unavailable');
  const worker = new Worker(bundle.mainWorker);
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  return db;
}

// Expose a semantic probe of crossfilter linking for the linked and operations
// scenes: whenever the brush selection changes, re-query how many rows the
// dependent views currently match. A working crossfilter must drop this count
// below the full row count under a brush and restore it when the brush resets.
function trackSelection(coordinator: Coordinator, select: any, table: string): void {
  runtime.selection = {table, count: meta.rows, updates: 0};
  let latest = 0;
  select.addEventListener('value', async () => {
    const ticket = ++latest;
    try {
      const predicate = select.predicate();
      const where = (Array.isArray(predicate) ? predicate : [predicate])
        .filter(Boolean)
        .map(clause => `(${clause})`)
        .join(' AND ') || 'TRUE';
      const [row] = await coordinator.query(
        `SELECT count(*) AS n FROM ${table} WHERE ${where}`,
        {type: 'json'}
      ) as Array<{n: number}>;
      if (ticket !== latest) return;
      runtime.selection = {table, count: Number(row.n), updates: (runtime.selection?.updates ?? 0) + 1};
    } catch (error) {
      if (ticket !== latest) return;
      runtime.selection = {table, count: -1, updates: (runtime.selection?.updates ?? 0) + 1, error: String(error)};
    }
  });
}

async function build(): Promise<void> {
  const database = await localDatabase();
  const connector = new DuckDBWASMConnector({duckdb: database});
  const coordinator = new Coordinator(connector, {cache: true, consolidate: true});
  const vg: any = createAPIContext({coordinator});
  const chart = document.querySelector<HTMLElement>('#chart')!;
  const status = document.querySelector<HTMLElement>('#status')!;
  status.textContent = 'building local table';

  if (scene === 'density') {
    await coordinator.exec(`
      CREATE TABLE points AS
      SELECT
        i,
        43 * sin(i * 0.017) + 19 * sin(i * 0.00031) + ((i % 97) / 97.0 - 0.5) * 13 AS x,
        31 * cos(i * 0.013) + 16 * sin(i * 0.00047) + ((i % 61) / 61.0 - 0.5) * 11 AS y,
        CASE WHEN i % 4 = 0 THEN 'north' WHEN i % 4 = 1 THEN 'east' WHEN i % 4 = 2 THEN 'south' ELSE 'west' END AS cohort
      FROM range(${meta.rows}) AS t(i)
    `);
    chart.replaceChildren(vg.plot(
      vg.heatmap(vg.from('points'), {x: 'x', y: 'y', bandwidth: 10, pixelSize: 1}),
      vg.intervalXY({as: vg.Selection.crossfilter()}),
      vg.colorScheme('magma'), vg.colorLegend(), vg.width(1120), vg.height(610),
      vg.marginLeft(62), vg.marginBottom(50), vg.xLabel('latent dimension α'), vg.yLabel('latent dimension β')
    ));
  } else if (scene === 'signals') {
    await coordinator.exec(`
      CREATE TABLE signals AS
      SELECT
        i % 100000 AS t,
        CAST(floor(i / 100000) AS INTEGER) AS series,
        sin((i % 100000) * (0.0007 + floor(i / 100000) * 0.00019))
          + 0.34 * cos((i % 100000) * 0.0051)
          + 0.12 * sin(i * 0.071) AS value
      FROM range(${meta.rows}) AS s(i)
    `);
    chart.replaceChildren(vg.plot(
      vg.denseLine(vg.from('signals'), {x: 't', y: 'value', z: 'series'}),
      vg.panZoomX(), vg.colorScheme('turbo'), vg.colorLegend(),
      vg.width(1120), vg.height(610), vg.marginLeft(62), vg.marginBottom(50),
      vg.xLabel('sample index'), vg.yLabel('signal amplitude'), vg.yGrid(true)
    ));
  } else if (scene === 'linked') {
    await coordinator.exec(`
      CREATE TABLE events AS
      SELECT
        i,
        24 * ((i % 1440) / 1440.0) AS hour,
        46 + 25 * sin((i % 1440) / 1440.0 * 2 * pi()) + 12 * cos(i * 0.017) + (i % 29) / 5.0 AS latency,
        CASE WHEN i % 3 = 0 THEN 'api' WHEN i % 3 = 1 THEN 'worker' ELSE 'storage' END AS service
      FROM range(${meta.rows}) AS e(i)
    `);
    const select = vg.Selection.crossfilter();
    trackSelection(coordinator, select, 'events');
    const points = vg.plot(
      vg.hexbin(vg.from('events'), {x: 'hour', y: 'latency', fill: vg.count(), binWidth: 13}),
      vg.intervalXY({as: select}), vg.colorScheme('viridis'),
      vg.width(1120), vg.height(395), vg.marginLeft(62), vg.xLabel('hour of day'), vg.yLabel('latency (ms)')
    );
    const profile = vg.plot(
      vg.barY(vg.from('events', {filterBy: select}), {x: vg.bin('hour'), y: vg.count(), fill: '#b14c6c'}),
      vg.width(1120), vg.height(205), vg.marginLeft(62), vg.marginBottom(44),
      vg.xLabel('selected hourly profile'), vg.yLabel('events'), vg.yGrid(true)
    );
    chart.replaceChildren(vg.vconcat(points, profile));
  } else {
    await coordinator.exec(`
      CREATE TABLE measures AS
      SELECT i,
        CAST(i % 96 AS INTEGER) AS period,
        CAST(floor(i / 96) % 8 AS INTEGER) AS cohort,
        CAST(floor(i / 768) % 6 AS INTEGER) AS subsystem,
        42 + 18 * sin(i * 0.017) + 11 * cos(i * 0.0043) + (i % 31) / 3.0 AS value,
        18 + 0.72 * (42 + 18 * sin(i * 0.017)) + 9 * sin(i * 0.031) AS metric,
        sin((i % 96) / 96.0 * 2 * pi()) AS vx,
        cos((i % 96) / 96.0 * 2 * pi()) AS vy
      FROM range(${meta.rows}) AS m(i)
    `);
    if(scene==='rankings')await coordinator.exec(`
      CREATE TABLE region_stats AS
      WITH aggregates AS (
        SELECT
          cohort,
          avg(value) AS mean_score,
          quantile_cont(value, 0.10) AS q10,
          quantile_cont(value, 0.90) AS q90,
          CAST(count(*) AS INTEGER) AS n
        FROM measures
        GROUP BY cohort
      )
      SELECT
        CAST(ROW_NUMBER() OVER (ORDER BY mean_score DESC) AS INTEGER) AS rank,
        cohort,
        mean_score,
        q10,
        q90,
        n,
        q90 + 3.4 AS q90_label_x,
        'n=' || CAST(n AS VARCHAR) AS count_label
      FROM aggregates
      ORDER BY rank
    `);
    const source=vg.from('measures');
    const wide={width:1120,height:610,marginLeft:72,marginBottom:54};
    let view:any;
    if(scene==='distribution')view=vg.plot(vg.densityY(source,{x:'value',fill:'cohort',fillOpacity:.32,stroke:'cohort'}),vg.colorScheme('turbo'),vg.colorLegend(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(wide.marginLeft),vg.xLabel('measurement'),vg.yLabel('density'));
    else if(scene==='regression')view=vg.plot(vg.hexbin(source,{x:'value',y:'metric',fill:vg.count(),binWidth:11}),vg.regressionY(source,{x:'value',y:'metric',stroke:'#ff6f91',strokeWidth:3}),vg.colorScheme('viridis'),vg.colorLegend(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(68),vg.xLabel('input metric'),vg.yLabel('response'));
    else if(scene==='retention')view=vg.plot(vg.lineY(source,{x:'period',y:vg.avg('value'),z:'cohort',stroke:'cohort',strokeWidth:2}),vg.dot(source,{x:'period',y:vg.avg('value'),fill:'cohort',r:2}),vg.colorScheme('turbo'),vg.colorLegend(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(68),vg.yGrid(true),vg.xLabel('period'),vg.yLabel('retained activity'));
    else if(scene==='quality-matrix')view=vg.plot(vg.cell(source,{x:'cohort',y:'subsystem',fill:vg.avg('value'),inset:2}),vg.colorScheme('magma'),vg.colorLegend(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(90),vg.xLabel('lifecycle phase'),vg.yLabel('subsystem'));
    else if(scene==='rankings'){
      const stats=vg.from('region_stats');
      runtime.rankings=await coordinator.query('SELECT rank, cohort, mean_score AS mean, q10, q90, n FROM region_stats ORDER BY rank',{type:'json'}) as NonNullable<Window['__mosaicDemo']>['rankings'];
      view=vg.plot(vg.ruleY(stats,{x1:'q10',x2:'q90',y:'rank',stroke:'#52758c',strokeOpacity:.55,strokeWidth:3}),vg.dot(stats,{x:'mean_score',y:'rank',fill:'cohort',r:11}),vg.text(stats,{x:'q90_label_x',y:'rank',text:'count_label',fill:'#52758c',fontSize:12}),vg.colorScheme('turbo'),vg.colorLegend(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(90),vg.xLabel('score · dot = region mean, bar = 10th–90th percentile'),vg.yLabel('region rank (1 = highest mean)'),vg.yReverse(true),vg.yTicks([1,2,3,4,5,6,7,8]));
    }
    else if(scene==='anomalies')view=vg.plot(vg.denseLine(source,{x:'i',y:'value'}),vg.spike(source,{x:'i',y:'metric',stroke:'#ff6f91'}),vg.panZoomX(),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(70),vg.xLabel('event index'),vg.yLabel('signal / anomaly'));
    else if(scene==='vector-field')view=vg.plot(vg.vector(source,{x:'period',y:'subsystem',rotate:'vx',length:'value',stroke:'cohort'}),vg.colorScheme('turbo'),vg.width(wide.width),vg.height(wide.height),vg.marginLeft(70),vg.xLabel('phase'),vg.yLabel('field band'));
    else if(scene==='small-multiples'){const views=Array.from({length:4},(_,k)=>vg.plot(vg.lineY(vg.from('measures',{filter:`subsystem = ${k}`}),{x:'period',y:vg.avg('value'),stroke:`${['#54d6c6','#7b9cff','#ff6f91','#ffd166'][k]}`}),vg.width(560),vg.height(280),vg.marginLeft(55),vg.yGrid(true),vg.xLabel(`service ${k+1}`)));view=vg.vconcat(vg.hconcat(views[0],views[1]),vg.hconcat(views[2],views[3]));}
    else {const select=vg.Selection.crossfilter();trackSelection(coordinator,select,'measures');view=vg.vconcat(vg.plot(vg.hexbin(source,{x:'period',y:'value',fill:vg.count(),binWidth:9}),vg.intervalXY({as:select}),vg.colorScheme('viridis'),vg.width(1120),vg.height(360),vg.marginLeft(68)),vg.plot(vg.barY(vg.from('measures',{filterBy:select}),{x:'cohort',y:vg.count(),fill:'cohort'}),vg.colorScheme('turbo'),vg.width(1120),vg.height(240),vg.marginLeft(68),vg.yGrid(true)));}
    chart.replaceChildren(view);
  }

  chart.addEventListener('pointerup', () => {
    runtime.interactions += 1;
    status.textContent = `${runtime.interactions} interactions`;
  });
  status.textContent = `${meta.rows.toLocaleString()} rows ready`;
  await new Promise(resolveFrame => requestAnimationFrame(() => requestAnimationFrame(resolveFrame)));
  await new Promise(resolveWait => setTimeout(resolveWait, 700));
  runtime.ready = true;
}

build().catch(error => {
  runtime.error = error instanceof Error ? error.stack ?? error.message : String(error);
  document.querySelector('#chart')!.innerHTML = `<pre class="error">${runtime.error}</pre>`;
  document.querySelector('#status')!.textContent = 'failed';
  console.error(error);
});
