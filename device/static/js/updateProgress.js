window.allProgressBars = {};

function updateProgress(msg, container) {
    let source = msg.source;
    let position = msg.position;
    let progress = msg.progress;
    let total = msg.total;
    let rate = msg.rate;
    let remaining = msg.remaining;
  
    let allProgressBars = window.allProgressBars;
  
    let desc = "[" + source + "] " + msg.desc;
  
    let statusContainer = document.getElementById(container);
    if (statusContainer == null) {
      console.log("Did not find " + container);
      return;
    }
  
    if (!Reflect.has(allProgressBars, source)) {
      allProgressBars[source] = {};
    }
    if (progress > -1) {
      let progressPercentage = (progress / total) * 100;
      allProgressBars[source][position] = progressPercentage;
    } else {
      if (Reflect.has(allProgressBars[source], position)) {
        Reflect.deleteProperty(allProgressBars[source], position)
      }
    }
  
    let statusHeader = document.getElementById(container + "-header");
    if (statusHeader != null) {
      let line = "<b>Progress</b> ";
  
      const bySource = Object.entries(allProgressBars);
      bySource.sort((a, b) => a[0].localeCompare(b[0]));
  
      for (const [titleSource, titleBars] of bySource) 
      {
        bars = Object.entries(titleBars);
        if(bars.length > 0)  {
          for (let index = 0; index < bars.length; index++) {
            const element = bars[index];
            //console.log(element);
            let pos = element[0];
            let prog = element[1];
            if( pos == 0) {
              line += " " + titleSource + ":" + Math.round(prog) + '%' ;
            }
          }
  
        }
      }
      statusHeader.innerHTML = line;
    } else {
      console.log("Did not find " + container  +"-header")
    }
  
  
    let sourceDiv = document.getElementById('source_' + source);
  
    if (!sourceDiv) {
      sourceDiv = document.createElement('div');
      sourceDiv.id = 'source_' + source;
      // sourceDiv.className = "card-body"
      statusContainer.appendChild(sourceDiv);
    }
  
    let positionDiv = document.getElementById('position_' + source + '_' + position);
  
    if (progress === -1) {
      if (positionDiv) {
        sourceDiv.removeChild(positionDiv);
      }
  
  
      return;
    }
  
    if (!positionDiv) {
      positionDiv = document.createElement('div');
      positionDiv.id = 'position_' + source + '_' + position;
      positionDiv.className = 'progress-container';
  
      let progressBarContainer = document.createElement('div');
      progressBarContainer.className = 'progress';
  
      let progressBar = document.createElement('div');
      progressBar.className = 'progress-bar overflow-visible text-dark';
      if (position == 0) {
        progressBar.className += ' bg-info';
      } else {
        progressBar.className += ' bg-warning';
      }
  
      progressBar.id = 'progress_' + source + '_' + position;
      progressBar.setAttribute('role', 'progressbar');
      progressBar.setAttribute('aria-valuemin', '0');
      progressBar.setAttribute('aria-valuemax', total);
      progressBar.style.setProperty("text-align", "left");
  
      progressBarContainer.appendChild(progressBar);
      positionDiv.appendChild(progressBarContainer);
      sourceDiv.appendChild(positionDiv);
    }
  
    let progressBar = document.getElementById('progress_' + source + '_' + position);
    let progressPercentage = (progress / total) * 100;
    progressBar.style.width = progressPercentage + '%';
    progressBar.setAttribute('aria-valuenow', progress);
    let innerHTML = desc ? desc + ': ' + Math.round(progressPercentage) + '%' : Math.round(progressPercentage) + '%';
    if (rate != null) {
      innerHTML += " : " + rate + " : " + remaining;
    }
    progressBar.innerHTML = innerHTML;
  
  }
  
  