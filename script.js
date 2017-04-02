var drawingData = {
	startX: 0,
	startY: 0,
	colors: [["rood", "blauw", "wit", "niets"]],
	kill: false
};

var modhash = window.reddit.modhash;
var timeSinceLastRetrieval = 5000;
var index = 0;
var sec = 0;
var currentVersion = 7;

var colorScheme = [
	"wit",
	"lgrijs",
	"dgrijs",
	"zwart",
	"roze",
	"rood",
	"oranje",
	"bruin",
	"geel",
	"lgroen",
	"groen",
	"lblauw",
	"blauw",
	"dblauw",
	"magenta",
	"paars",
	"niets"
];

function replaceTextWithNumbers(){
	for (var i = 0; i < drawingData.colors[0].length * drawingData.colors.length; i++) {
		var width = drawingData.colors[0].length;
		var height = drawingData.colors.length;
		var tempx = i % width;
		var tempy = Math.floor(i / width);
		console.log(width, height, tempx, tempy, drawingData.colors);		
		for (var i = 0; i < colorScheme.length; i++) {
		  	if(drawingData.colors[tempy][tempx] == colorScheme[i]){
				drawingData.colors[tempy][tempx] = i;
			    	break;
		    	}
		}
	}
}

function retrieveAndDraw(doDraw) {

		// retrieve data
		const url = 'https://raw.githubusercontent.com/Sadye/rPlace/master/data.json' + '?no-cache=' + (new Date).getTime();

		//TODO implement random selection of multiple files
		//better to be handled @ serer

		fetch(url)
		.then((resp) => resp.json())
		.then(function(data) {
			drawingData.startX = data.startX;
			drawingData.starty = data.startY;
			drawingData.colors = data.colors;

			if (currentVersion < data.newVersion) {
				document.body.innerHTML = '<center><br><br><h1>Je script is verouderd! Download alsjeblieft de nieuwe update (v' + data.newVersion + '). <br><br><br><br> <a href=https://raw.githubusercontent.com/Sadye/rPlace/master/script.js>Script</a> | <a href="https://discord.gg/EU4NhBn">Discord</a></h1></center>';
			}

			index = Math.floor(Math.random() * (drawingData.colors[0].length * drawingData.colors.length));
			timeSinceLastRetrieval = 0;
			replaceTextWithNumbers();

			if (doDraw) draw(0);

		})
		.catch(function(error) {
			console.log(error);
			setTimeout(() => retrieveAndDraw(doDraw), 10 * 1e3);
		});
}

function draw(seconds) {
	if (drawingData.kill) {
		//setTimeout(() => retrieveAndDraw(true), 250 * 1e3);
		// it's automatically updated every 250 s
		return;
	}
	//retrieveAndDraw(false);
	var width = drawingData.colors[0].length;
	var height = drawingData.colors.length;
	index++;
	console.log(width, height, index);
    index = index % (width * height);
    sec = seconds = Math.ceil(seconds);
    setTimeout(() => {
        const x = index % width;
        const y = Math.floor(index / width);
        // vanaf nu kan een flagcolor -1 zijn, dan wordt die kleur als altijd correct gezien

        const flagColor = drawingData.colors[y][x];
        if (flagColor == 16) {
        	return draw(0);
        }
        // const xChange = flagColor != drawingData.colors[y][x - 1] || flagColor != drawingData.colors[y][x + 1];
        // const yChange = (drawingData.colors[y - 1] && flagColor != drawingData.colors[y - 1][x]) || (drawingData.colors[y + 1] && flagColor != drawingData.colors[y + 1][x]);
        // if  (!xChange && !yChange) {
        //     return draw(0);
        // }
        const ax = x + drawingData.startX;
        const ay = y + drawingData.startY;

        $.get("https://www.reddit.com/api/place/pixel.json?x=" + ax + "&y=" + ay)
        .then(res => {
        	console.log(res);
            if (res.color == flagColor) {
                console.log((ax + ", " + ay) + " worden overgeslagen omdat ze al kloppen!");
                return draw(1);
            }
            var color = colorScheme[flagColor];
            console.log("Pixel tekenen op locatie " + ax + ", " + ay + " ("+color+") (https://www.reddit.com/r/place/#x=" + ax + "&y=" + ay + ")");
            $.ajax({ url: "https://www.reddit.com/api/place/draw.json", type: "POST",
                headers: { "x-modhash": modhash }, data: { x: ax, y: ay, color: flagColor }
            })
            .done(data => {
            	//data=json.parse(data);
            	draw(data.wait_seconds);
            	console.log(data);
            	return;
            })
            .error(data => {
            	//data = JSON.parse(data);
            	draw(data.responseJSON.wait_seconds);
            	return;
            });
        });
    }, seconds * 1000);
}

replaceTextWithNumbers();
retrieveAndDraw(true);
window.setInterval( () => console.log("Pixel tekenen over " + (sec) + " seconden. Data ophalen " + (timeSinceLastRetrieval > 250 ? "na volgende pixel. " : "over " + (250-timeSinceLastRetrieval) + " seconden!")), 10 * 1e3);
window.setInterval(() => {sec--;timeSinceLastRetrieval++}, 1e3);
window.setInterval(() => retrieveAndDraw(false), 250 * 1e3);
