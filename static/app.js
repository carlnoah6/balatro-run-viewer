const API='/balatro/api';
let jokerCatalog=[];
let refreshTimer=null;
let currentRunId=null;
let currentRunCode=null;

function ssUrl(rc,fn){return '/balatro/screenshots/'+rc+'/screenshots/'+fn}

function getRoute(){
  var p=location.pathname;
  var m=p.match(/\/balatro\/game\/([^\/]+)/);
  if(m&&m[1])return{view:'detail',runCode:m[1]};
  return{view:'list'};
}
function navigateTo(path){
  stopRefresh();
  if(path)history.pushState(null,'','/balatro/game/'+path);
  else history.pushState(null,'','/balatro/');
  renderRoute();
}
function renderRoute(){
  var r=getRoute();
  if(r.view==='detail'&&r.runCode){
    document.getElementById('app').innerHTML='<div style="padding:2rem;color:#aaa">加载中...</div>';
    fetch(API+'/runs/by-code/'+encodeURIComponent(r.runCode))
      .then(function(resp){return resp.ok?resp.json():null})
      .then(function(d){
        if(d&&d.run){currentRunId=d.run.id;currentRunCode=d.run.run_code;fetchAndRenderDetail()}
        else{history.replaceState(null,'','/balatro/');renderList()}
      })
      .catch(function(){history.replaceState(null,'','/balatro/');renderList()});
  }else{
    if(location.pathname!=='/balatro/'&&location.pathname!=='/balatro')history.replaceState(null,'','/balatro/');
    renderList();
  }
}
window.addEventListener('popstate',renderRoute);
function stopRefresh(){if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null}}

async function loadJokerCatalog(){
  try{var r=await fetch(API+'/jokers/catalog');var d=await r.json();jokerCatalog=d.jokers||[]}catch(e){}
}
function findJoker(name){
  var n=name.toLowerCase().trim();
  return jokerCatalog.find(function(j){return j.name_en.toLowerCase()===n||j.name_zh===name});
}

var currentPage=1;
function renderList(){
  stopRefresh();
  loadRuns(1);
}
async function loadRuns(page){
  page=page||1;currentPage=page;
  try{
    var r=await fetch(API+'/runs?page='+page+'&per_page=20&sort=played_at&order=desc');
    var d=await r.json();
    var runs=d.runs||[];
    var h='<table class="run-table"><thead><tr><th>编号</th><th>结果</th><th>策略</th><th>种子</th><th>Ante</th><th>出牌</th><th>弃牌</th><th>Rule率</th><th>耗时</th><th>成本</th><th>时间</th></tr></thead><tbody>';
    for(var i=0;i<runs.length;i++){
      var run=runs[i];
      var badge=run.status==='running'?'<span class="badge running">运行中</span>':(run.won?'<span class="badge win">通关</span>':'<span class="badge loss">失败</span>');
      var rd=run.rule_decisions||0,ld=run.llm_decisions||0,td=rd+ld;
      var ratio=td>0?Math.round(rd/td*100)+'%':'-';
      var dur=run.duration_seconds?Math.round(run.duration_seconds/60)+'m':'-';
      var cost=run.llm_cost_usd?'$'+Number(run.llm_cost_usd).toFixed(4):'-';
      var t=run.played_at?new Date(run.played_at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
      var sname=run.strategy_name||'-';
      var stratCell=run.strategy_sid?'<a href="/balatro/strategy/'+run.strategy_sid+'" style="color:var(--gold);font-size:.8rem" onclick="event.stopPropagation()">'+sname+'</a>':'-';
      var seed=run.seed||'-';if(seed.length>8)seed=seed.substring(0,8);
      h+='<tr onclick="navigateTo(\''+run.run_code+'\')"><td class="run-code">'+run.run_code+'</td><td>'+badge+'</td><td>'+stratCell+'</td><td style="font-family:monospace;font-size:.8rem;color:var(--muted)">'+seed+'</td><td>'+run.final_ante+'</td><td>'+(run.hands_played||0)+'</td><td>'+(run.discards_used||0)+'</td><td>'+ratio+'</td><td>'+dur+'</td><td>'+cost+'</td><td>'+t+'</td></tr>';
    }
    h+='</tbody></table>';
    document.getElementById('app').innerHTML=h;
  }catch(e){document.getElementById('app').innerHTML='<div style="padding:2rem;color:#f87171">加载失败</div>'}
}

async function fetchAndRenderDetail(){
  try{
    var r=await fetch(API+'/runs/'+currentRunId);
    var data=await r.json();
    renderDetail(data,currentRunCode);
    if(data.run&&data.run.status==='running'&&!refreshTimer){
      refreshTimer=setInterval(fetchAndRenderDetail,5000);
    }else if(data.run&&data.run.status!=='running'){
      stopRefresh();
    }
  }catch(e){document.getElementById('app').innerHTML='<div style="padding:2rem;color:#f87171">加载失败: '+e+'</div>'}
}

function blindFromCap(cap,evType){
  if(cap.indexOf('商店')>=0)return '商店';
  if(cap.indexOf('小盲')>=0)return '小盲';
  if(cap.indexOf('大盲')>=0)return '大盲';
  if(cap.indexOf('Boss')>=0)return 'Boss';
  if(cap.indexOf('游戏结束')>=0||evType==='game_over')return '结束';
  if(cap.indexOf('开始')>=0||evType==='game_start')return '开始';
  return '';
}
function anteFromCap(cap){
  var m=cap.match(/第(\d+)关/);
  return m?parseInt(m[1]):0;
}

function renderDetail(data,runCode){
  var run=data.run, jokers=data.jokers||[], screenshots=data.screenshots||[];
  var rc=run.run_code||runCode||run.id;
  var isRunning=run.status==='running';
  var dur=run.duration_seconds?Math.round(run.duration_seconds/60)+'分钟':'-';
  var cost=run.llm_cost_usd?'$'+Number(run.llm_cost_usd).toFixed(4):'-';
  var rd=run.rule_decisions||0,ld=run.llm_decisions||0,td=rd+ld;
  var ratio=td>0?Math.round(rd/td*100)+'%':'-';

  var html='<a class="back-btn" onclick="navigateTo(\'\');return false" href="/balatro/">← 返回列表</a>';
  var icon=isRunning?'🔄':(run.won?'🏆':'💀');
  var statusBadge=isRunning?' <span class="badge running">运行中</span>':'';
  html+='<div class="detail-header"><h2>'+icon+' '+rc+statusBadge+'</h2>';
  // Strategy + seed line
  var stratInfo=data.strategy?'<a href="/balatro/strategy/'+data.strategy.id+'" style="color:var(--gold)">'+data.strategy.name+'</a>':'未知';
  html+='<div style="font-family:monospace;font-size:.9rem;color:var(--muted);margin:.5rem 0">种子: '+(run.seed||'未知')+' | 策略: '+stratInfo+'</div>';
  html+='<div class="detail-stats">';
  var stats=[{v:'Ante '+run.final_ante,l:'关卡'},{v:run.hands_played||0,l:'出牌'},{v:run.discards_used||0,l:'弃牌'},{v:run.purchases||0,l:'购买'},{v:ratio,l:'Rule率'},{v:dur,l:'耗时'},{v:cost,l:'LLM成本'}];
  for(var si=0;si<stats.length;si++){
    html+='<div class="stat"><div class="val">'+stats[si].v+'</div><div class="lbl">'+stats[si].l+'</div></div>';
  }
  html+='</div></div>';

  // Jokers
  if(jokers.length){
    html+='<div class="section"><h3>🃏 小丑牌 ('+jokers.length+')</h3><div class="joker-grid">';
    for(var ji=0;ji<jokers.length;ji++){
      var j=jokers[ji];
      var cj=findJoker(j.name);
      var imgSrc=cj&&cj.image?'/balatro/joker-images/'+cj.image:'';
      html+='<div class="joker-card">';
      if(imgSrc)html+='<img src="'+imgSrc+'" alt="'+j.name+'">';
      html+='<div class="joker-info"><div class="name-en">'+j.name+'</div>';
      if(cj&&cj.name_zh)html+='<div class="name-zh">'+cj.name_zh+'</div>';
      if(cj){var eff=cj.effect_zh||cj.effect_en||'';if(eff)html+='<div class="effect">'+eff+'</div>';}
      html+='</div></div>';
    }
    html+='</div></div>';
  }

  // Feed
  html+='<div class="section"><h3>📷 游戏过程 ('+screenshots.length+' 张)';
  if(isRunning)html+=' <span class="badge running">实时更新中</span>';
  html+='</h3><div class="feed">';

  var lastKey='';
  for(var i=0;i<screenshots.length;i++){
    var s=screenshots[i];
    var url=ssUrl(rc,s.filename);
    var cap=s.caption||s.event_type||'';
    var ante=anteFromCap(cap);
    var blind=blindFromCap(cap,s.event_type||'');
    var key='a'+ante+'-'+blind;

    if(key!==lastKey&&blind){
      var divLabel=ante>0?'第'+ante+'关 '+blind:blind;
      html+='<div class="blind-divider">'+divLabel+'</div>';
      lastKey=key;
    }

    var src='';
    if(cap.indexOf('[Rule]')>=0)src='rule';
    else if(cap.indexOf('[LLM]')>=0)src='llm';

    html+='<div class="feed-entry">';
    if(cap){
      html+='<div class="caption">'+cap;
      if(src)html+=' <span class="source-tag '+src+'">'+src.toUpperCase()+'</span>';
      html+='</div>';
    }
    if(s.estimated_score&&s.actual_score!=null){
      var est=s.estimated_score,act=s.actual_score;
      var err=s.score_error||0;
      var errPct=Math.round(err*100);
      var errClass=Math.abs(err)<0.2?'good':(Math.abs(err)<0.5?'ok':'bad');
      html+='<div class="score-bar">';
      html+='<span class="score-est">估分 '+est+'</span>';
      html+='<span class="score-arrow">→</span>';
      html+='<span class="score-act">实际 '+act+'</span>';
      html+='<span class="score-err '+errClass+'">'+(err>=0?'+':'')+errPct+'%</span>';
      html+='</div>';
    }
    html+='<img class="screenshot" src="'+url+'" alt="" onclick="openLightbox(this.src)" loading="lazy" onerror="this.style.display=\'none\'">';
    html+='</div>';
  }
  html+='</div></div>';

  document.getElementById('app').innerHTML=html;
}

function openLightbox(src){document.getElementById('lightbox-img').src=src;document.getElementById('lightbox').classList.add('active')}
function closeLightbox(){document.getElementById('lightbox').classList.remove('active')}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeLightbox()});

async function init(){
  await loadJokerCatalog();
  renderRoute();
}
init();
