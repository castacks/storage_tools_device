# AIRlab Storage Tools Device

When you need to get your files from here to the server.

## Installation

Install docker compose.  

Clone this repo.

Update `config/config.yaml` to match your environment.  Things you should update:

* `source`.  This must be unique.  
* `API_KEY_TOKEN`.  The api key that your admin gave you.
* `watch`.  The list of directories that have your robot's files.

Update the `env.sh` to match your system.

* `CONFIG_FILE`.  If you have multiple config files, make sure `CONFIG_FILE` points to the one you want to use.
* `DATA_DIR`. This is the top level data directory that all of the `watch` dirs share.  

## Build

``` bash
. env.sh
docker compose build
```

## Run

``` bash
. env.sh 
docker compose up
```

## Stop

``` bash
. env.sh
docker compose down
```
