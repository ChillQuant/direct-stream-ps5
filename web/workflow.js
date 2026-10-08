'use strict';
let setupTesting = false;
let workflowSignature = '';
let workflowSeenCompleted = null;
let recoveryPasswordJob = null;
function setupStep(step) {
  ['setup-prepare','setup-address','setup-result'].forEach((id,i)=>$(id).hidden=i!==step);
  document.querySelectorAll('[data-setup-step]').forEach(el=>{
    if(Number(el.dataset.setupStep)===step) el.setAttribute('aria-current','step'); else el.removeAttribute('aria-current');
    el.classList.toggle('step-done',Number(el.dataset.setupStep)<step);
  });
}
window.openConnectionSetup = function() {
  if (!state) { toast('Waiting for the app connection. Try again in a moment.',true); return; }
  if (!$('setup-dialog').open) {
    $('setup-host').value=state.settings.host || '';
    $('setup-port').value=state.settings.port || 1337;
    $('setup-folder').value=state.settings.folder || '/data/homebrew';
    $('setup-username').value=state.settings.username || 'anonymous';
    $('setup-password').value='';
    $('setup-clear-password').checked=false;
    $('setup-clear-password-row').hidden=!state.has_password;
    $('setup-password-hint').textContent=state.has_password ? 'A password is already saved. Leave blank to keep it.' : 'Leave blank if your server does not require a password.';
    $('setup-error').textContent='';
    setupStep(setupTesting ? 2 : 0);
    $('setup-dialog').showModal();
  }
};
$('setup-next').onclick=()=>{setupStep(1);$('setup-host').focus();};
$('setup-back').onclick=()=>setupStep(0);
$('setup-edit').onclick=()=>{setupStep(1);$('setup-host').focus();};
$('setup-finish').onclick=()=>{$('setup-dialog').close();page('transfers');};
$('setup-form').addEventListener('submit',async e=>{
  e.preventDefault();
  if(!state || setupTesting)return;
  const folder=$('setup-folder').value.trim();
  if(!folder.startsWith('/')){$('setup-error').textContent='Use a console folder path starting with /, such as /data/homebrew.';return;}
  $('setup-test').disabled=true;$('setup-error').textContent='';
  try {
    const settings={...state.settings,host:$('setup-host').value.trim(),port:$('setup-port').value,folder,username:$('setup-username').value.trim() || 'anonymous'};
    if($('setup-clear-password').checked)settings.password='';
    else if($('setup-password').value || !state.has_password)settings.password=$('setup-password').value;
    await api('settings',settings);
    await api('diagnostic',{kind:'connection'});
    setupTesting=true;
    setupStep(2);
    $('setup-result-title').textContent='Checking your console…';
    $('setup-result-copy').textContent='Testing the address, port and file server login.';
    $('setup-result-icon').className='setup-result-icon checking';
    $('setup-test-details').hidden=true;
    $('setup-edit').disabled=true;$('setup-finish').hidden=true;
    await poll();
  } catch(err) {$('setup-error').textContent=err.message;} finally {$('setup-test').disabled=false;}
});
function recoveryFor(job) {
  const text=String(job.error_info?.summary || job.detail || '').toLowerCase();
  if(/archive.*password|password.*archive|encrypted|decrypt|wrong password/.test(text))return {title:'This archive needs a password',copy:'Enter the password supplied with the archive, then queue another attempt.',action:'password',label:'Enter password'};
  if(job.kind==='url' && /expired|\b40[134]\b|http.*forbidden|download link|source.*not found/.test(text))return {title:'The download link needs attention',copy:'It may have expired or become unavailable. Replace it with a fresh link from the same file.',action:'link',label:'Replace link'};
  if(/disk.*full|no space|insufficient.*space|not enough.*space|storage/.test(text))return {title:'There may not be enough storage',copy:'Check free space on the console and this device if local extraction is enabled. Retry after freeing space.',action:'destination',label:'Open destination'};
  if(/refused|timed? ?out|unreachable|connection.*lost|broken pipe|ftp|login/.test(text))return {title:'The connection needs a check',copy:'Keep the PS5 file server running and verify its address, port and password. Your partial transfer may still be available.',action:'connect',label:'Check connection'};
  if(/extract|archive|unar|unrar|corrupt/.test(text))return {title:'The archive could not be unpacked',copy:'Check that all archive parts are present and review the extraction details before retrying.',action:'details',label:'Review details'};
  return {title:'This transfer needs another look',copy:'Review the details, or queue another attempt when the source and console are ready.',action:'retry',label:'Queue retry'};
}
function isArchiveJob(job) { return !!(job.is_archive || job.is_zip || /\.(zip|rar|7z)$/i.test(job.name || '')); }
function destinationFor(job) {
  return job.extract_mode==='ps5' && job.decompress!==false && isArchiveJob(job)
    ? job.unrar_extract_location || state.settings.unrar_extract_location || job.folder
    : job.folder || state.settings.folder;
}
function openDestination(job) {
  const path=destinationFor(job);
  $('browse-path').value=path || '/data/homebrew';
  // Prevent page() from issuing its own first-load request with the wrong path.
  lastFilesLoaded=true;
  page('files');
  $('browse-form').requestSubmit();
}
function archiveOutcome(job) {
  if(!isArchiveJob(job))return job.kind==='folder'?'Folder transfer completed.':'File transfer completed.';
  if(job.decompress===false || job.extract_mode==='none')return 'Sent as a compressed archive; no extraction requested.';
  if(job.extract_mode==='ps5')return job.unrar_delete_after===false ? 'Console extraction completed. Archive retention was enabled.' : job.unrar_delete_after===true ? 'Console extraction completed. Automatic archive cleanup was enabled; check the extraction log to confirm cleanup.' : 'Console extraction completed. Check the extraction log for archive cleanup details.';
  return 'Processed on this device before transfer. Temporary staging, when used, is cleaned up by the transfer engine.';
}
window.renderWorkflow = function(s) {
  if(setupTesting && s.diagnostic?.kind==='connection' && ['done','error'].includes(s.diagnostic.state)) {
    setupTesting=false;
    const ok=s.diagnostic.state==='done' && s.connection.state==='connected';
    $('setup-result-title').textContent=ok?'Your console is connected.':'We couldn’t reach your console.';
    $('setup-result-copy').textContent=ok?'You’re ready to add a file, folder or download link.':'Check that the file server is running, both devices share a network, and the address and port match the console.';
    $('setup-result-icon').className='setup-result-icon '+(ok?'success':'needs-attention');
    $('setup-result-icon').innerHTML=`<svg><use href="#${ok?'i-check':'i-alert'}"/></svg>`;
    $('setup-test-message').textContent=s.diagnostic.message || s.connection.message || '';
    $('setup-test-details').hidden=false;
    $('setup-edit').disabled=false;$('setup-finish').hidden=!ok;
  }
  const busy=!!s.active || s.diagnostic?.state==='running';
  $('setup-test').disabled=busy || setupTesting;
  $('setup-test').title=busy?'Pause active transfers and finish diagnostics before changing the connection.':'';
  const active=s.jobs.find(j=>j.id===s.active);
  $('live-panel').hidden=!active && !s.jobs.some(j=>j.state==='queued');
  const journey=$('transfer-journey');
  journey.hidden=!active;
  $('journey-description').hidden=!active;
  if(active) {
    const detail=String(active.detail || '').toLowerCase();
    const extracts=active.decompress!==false && (active.is_archive || active.is_zip || /\.(zip|rar|7z)$/i.test(active.name || ''));
    const local=extracts && (active.extract_mode==='mac' || active.staged_extraction);
    const stages=local?['Prepare','Extract locally','Transfer','Complete']:extracts?['Prepare','Transfer','Extract on PS5','Complete']:['Prepare','Transfer','Complete'];
    const extracting=/extract|unpack|decompress/.test(detail);
    let current=active.state==='completed'?stages.length-1:active.state==='starting'?0:extracting?(local?1:2):(local?2:1);
    if(/staging|prepar|probing|resolv/.test(detail))current=0;
    journey.innerHTML=stages.map((label,i)=>`<span class="journey-stage ${i<current?'done':i===current?'current':''}" ${i===current?'aria-current="step"':''}><b>${i<current?'✓':i+1}</b>${escaped(label)}</span>`).join('');
    $('journey-description').textContent=active.state==='retrying'?'The connection was interrupted. The app is retrying automatically.':active.state==='paused'?'Paused. Resume when you’re ready.':active.state==='pausing'?'Finishing the current operation before pausing…':active.state==='cancelling'?'Stopping the transfer…':current===0?'Checking the source and preparing the destination.':extracting?(local?'Unpacking on this device before sending the files.':'Upload finished. Your PS5 is unpacking the archive.'): 'Sending data to your console. You can pause this transfer at any time.';
  }
  const completed=s.jobs.filter(j=>j.state==='completed');
  const ids=new Set(completed.map(j=>j.id));
  const justCompleted=workflowSeenCompleted && completed.some(j=>!workflowSeenCompleted.has(j.id));
  workflowSeenCompleted=ids;
  const failed=s.jobs.filter(j=>j.state==='failed');
  $('recovery-panel').hidden=!failed.length;
  $('completion-panel').hidden=!completed.length;
  if(justCompleted) {
    $('workflow-announcement').textContent=`${completed.length} ${completed.length===1?'transfer is':'transfers are'} complete.`;
    $('completion-panel').classList.remove('just-completed');
    requestAnimationFrame(()=>$('completion-panel').classList.add('just-completed'));
  }
  const hostLabel = {Darwin:'Mac',Windows:'Windows PC',Linux:'Linux device'}[s.host_platform] || 'host device';
  $('local-path').placeholder=sourceKind==='folder' ? (s.host_platform==='Windows'?'C:\\Users\\you\\Downloads\\game-folder':'/path/to/game-folder') : (s.host_platform==='Windows'?'C:\\Users\\you\\Downloads\\game.pkg':'/path/to/game.pkg');
  $('local-path-label').textContent=sourceKind==='folder'?`Folder path on the ${hostLabel} running Direct Stream`:`File path on the ${hostLabel} running Direct Stream`;
  const signature=JSON.stringify([[...failed,...completed].map(j=>[j.id,j.state,j.detail,j.folder,j.extract_mode,j.unrar_extract_location,j.unrar_delete_after,j.total]),s.jobs.length,s.settings.folder,s.settings.unrar_extract_location]);
  if(signature===workflowSignature)return;
  workflowSignature=signature;
  $('recovery-caption').textContent=`${failed.length} ${failed.length===1?'transfer needs':'transfers need'} attention. Completed files are unaffected.`;
  $('recovery-items').innerHTML=failed.map(j=>{const r=recoveryFor(j);return `<article class="recovery-item"><div><strong>${escaped(j.name)}</strong><h3>${escaped(r.title)}</h3><p>${escaped(r.copy)}</p></div><div class="workflow-actions"><button type="button" class="button outline small" data-workflow-action="${r.action}" data-workflow-job="${j.id}">${r.label}</button>${r.action!=='retry'?`<button type="button" class="button outline small" data-workflow-action="retry" data-workflow-job="${j.id}">Queue retry</button>`:''}${r.action!=='details'?`<button type="button" class="text-button" data-workflow-action="details" data-workflow-job="${j.id}">Details</button>`:''}</div></article>`;}).join('');
  const total=completed.reduce((n,j)=>n+(j.total || j.transferred || 0),0);
  $('completion-summary').textContent=`${completed.length} ${completed.length===1?'transfer':'transfers'} completed${total?' · '+bytes(total)+' transferred':''}.`;
  $('completion-heading').textContent=s.jobs.length===completed.length?'All set. Your files have arrived.':'Another file, delivered.';
  $('completion-items').innerHTML=completed.slice(-5).reverse().map(j=>`<article class="completion-item"><div><strong>${escaped(j.name)}</strong><p class="completion-destination">${escaped(destinationFor(j) || '/data/homebrew')}</p><p>${escaped(archiveOutcome(j))}</p></div><div class="workflow-actions"><button type="button" class="button outline small" data-workflow-action="destination" data-workflow-job="${j.id}">Open destination <svg><use href="#i-chevron-right"/></svg></button>${j.extract_mode==='ps5' && isArchiveJob(j)?`<button type="button" class="text-button" data-workflow-action="log" data-workflow-job="${j.id}">Extraction log</button>`:''}</div></article>`).join('')+(completed.length>5?'<p class="field-hint">Showing the latest five entries in queue order. All completed transfers remain in the queue.</p>':'');
};
document.addEventListener('click',async e=>{
  if(e.target.closest('[data-setup]')){window.openConnectionSetup();return;}
  const button=e.target.closest('[data-workflow-action]');if(!button)return;
  const job=state?.jobs.find(j=>String(j.id)===button.dataset.workflowJob);if(!job)return;
  const action=button.dataset.workflowAction;
  if($('error-dialog').open)$('error-dialog').close();
  if(action==='connect'){window.openConnectionSetup();setupStep(1);return;}
  if(action==='details'){openErrorDialog(job);return;}
  if(action==='link'){await jobAction('edit',job.id);return;}
  if(action==='destination'){openDestination(job);return;}
  if(action==='log'){await openUnrarLogDialog();return;}
  if(action==='password'){recoveryPasswordJob=job.id;$('recovery-password-name').textContent=job.name;$('recovery-password-input').value='';$('recovery-password-error').textContent='';$('recovery-password-dialog').showModal();return;}
  if(action==='retry'){button.disabled=true;try{await jobAction('resume',job.id);await poll();}finally{button.disabled=false;}}
});
$('recovery-password-form').addEventListener('submit',async e=>{
  e.preventDefault();const button=e.submitter;button.disabled=true;
  try{await api('action',{action:'set_password',id:recoveryPasswordJob,password:$('recovery-password-input').value});await api('action',{action:'resume',id:recoveryPasswordJob});$('recovery-password-dialog').close();toast('Password saved. Start the queue when you’re ready.');await poll();}catch(err){$('recovery-password-error').textContent=err.message;}finally{button.disabled=false;}
});
window.workflowDisconnected=function(){if(setupTesting){setupTesting=false;$('setup-result-title').textContent='The app connection was lost.';$('setup-result-copy').textContent='Reopen Direct Stream, then test again. No connection result is available yet.';$('setup-edit').disabled=false;$('setup-finish').hidden=true;}};

window.configureRecoveryDialog=function(job) {
  let button=$('error-recovery-action');
  if(!button){button=document.createElement('button');button.id='error-recovery-action';button.type='button';button.className='button primary small';document.querySelector('#error-dialog .error-summary-box').append(button);}
  const remedy=recoveryFor(job);
  button.textContent=remedy.action==='details'?'Queue retry':remedy.label;
  button.dataset.workflowAction=remedy.action==='details'?'retry':remedy.action;
  button.dataset.workflowJob=job.id;
};
