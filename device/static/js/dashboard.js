const configFields = ["watch", "servers", "include_suffix"];

// Function to add list items dynamically
function addListItem(field) {
    const newItem = document.getElementById(`new-${field}-item`).value.trim();
    if (newItem) {
        const listElement = document.getElementById(`${field}-list`);
        const itemElement = document.createElement("div");
        itemElement.className = "input-group mb-1";
        itemElement.innerHTML = `
            <input type="text" class="form-control" value="${newItem}" readonly>
            <button class="btn btn-danger" onclick="removeListItem(this)">&#128465;</button>
        `;
        listElement.appendChild(itemElement);
        document.getElementById(`new-${field}-item`).value = ""; // Clear input field
    }
}

// Function to remove list items
function removeListItem(element) {
    element.parentElement.remove();
}

// Function to save configuration
function saveConfig() {
    const config = {
        project: document.getElementById("project_name").value,
        robot_name: document.getElementById("robot_name").value,
        API_KEY_TOKEN: document.getElementById("API_KEY_TOKEN").value,
        watch: getListItems('watch'),
        local_tz: document.getElementById("local_tz").value,
        servers: getListItems('servers'),
        threads: parseInt(document.getElementById("threads").value),
        include_suffix: getListItems('include_suffix'),
        wait_s: parseInt(document.getElementById("wait_s").value)
    };

    fetch("/save_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config)
    }).then(response => {
        if (response.ok) {
            alert("Configuration saved successfully!");
        } else {
            alert("Failed to save configuration.");
        }
    });
}

// Function to get list items
function getListItems(field) {
    const items = [];
    document.getElementById(`${field}-list`).querySelectorAll('input').forEach(input => {
        items.push(input.value);
    });
    return items;
}

// Function to refresh configuration
function refreshConfig() {
    fetch("/get_config")
        .then(response => response.json())
        .then(config => {
            document.getElementById("project_name").value = config.project || "";
            document.getElementById("robot_name").value = config.robot_name || "";
            document.getElementById("API_KEY_TOKEN").value = config.API_KEY_TOKEN || "";
            document.getElementById("local_tz").value = config.local_tz || "";
            document.getElementById("threads").value = config.threads || 4;
            document.getElementById("wait_s").value = config.wait_s || 2;

            // Populate list fields
            configFields.forEach(field => {
                console.log(field, `${field}-list`);
                const listElement = document.getElementById(`${field}-list`);
                listElement.innerHTML = '';  // Clear existing items
                (config[field] || []).forEach(item => addListItemToField(field, item));
            });
        });
}

// Helper function to add list item from server data
function addListItemToField(field, value) {
    const listElement = document.getElementById(`${field}-list`);
    const itemElement = document.createElement("div");
    itemElement.className = "input-group mb-1";
    itemElement.innerHTML = `
        <input type="text" class="form-control" value="${value}" readonly>
        <button class="btn btn-danger" onclick="removeListItem(this)">&#128465;</button>
    `;
    listElement.appendChild(itemElement);
}


function disconnect() {
    fetch("/disconnect")
}

// Refresh config on page load
document.addEventListener('DOMContentLoaded', refreshConfig);


// connect to web socket to get connection status. 

$(document).ready(function () {

    let protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
    let url = protocol + "//" + document.domain + ':' + location.port;

    var socket = io.connect(url, { transports: ['websocket']});


    socket.on("connect", function(){
        let deviceStatusDiv = document.getElementById('device_connection_status');
        deviceStatusDiv.className = "connection_status_online";
        deviceStatusDiv.textContent = "Device: Online"
    
    });

    socket.on("disconnect", function() {
        console.log("Disconnected from device")
        let deviceStatusDiv = document.getElementById('device_connection_status');
        deviceStatusDiv.className = "connection_status_offline";
        deviceStatusDiv.textContent = "Device: Offline";

        let serverStatusDiv = document.getElementById('server_connection_status');
        serverStatusDiv.className = "connection_status_unknown";
        serverStatusDiv.textContent = "Server: Unknown";
    });
    

    socket.on("server_connect", function(msg) {
        let serverStatusDiv = document.getElementById('server_connection_status');
        serverStatusDiv.className = "connection_status_online";
        serverStatusDiv.textContent = "Server: Online";

        let nameInput = document.getElementById("connection_server_name")
        nameInput.value = msg.name 

    });

    socket.on("server_disconnect", function() {
        let serverStatusDiv = document.getElementById('server_connection_status');
        serverStatusDiv.className = "connection_status_offline";
        serverStatusDiv.textContent = "Server: Disconnected";

        let nameInput = document.getElementById("connection_server_name")
        nameInput.value = ""

    });

});