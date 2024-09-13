# AIRlab Storage Tools Device

When you need to get your files from here to the server. The Storage Tools Device will run on any platform that supports Docker.  

## Requirements

* [Docker Compose](https://docs.docker.com/compose/install/standalone/)

## Installation

* Install [Docker Compose](https://docs.docker.com/compose/install/standalone/) following the instructions for your operating system.  

* Clone this repo.

  ```bash
  cd /opt 
  git clone https://github.com/castacks/storage_tools_device
  cd stroage_tools_device
  ```

* Update `config/config.yaml` to match your environment.  Things you should update:

  * `API_KEY_TOKEN`.  The api key that your admin gave you.
  * `watch`.  The list of directories that have your robot's files.

* Update the `env.sh` to match your system.

  * `CONFIG_FILE`.  If you have multiple config files, make sure `CONFIG_FILE` points to the one you want to use.
  * `DATA_DIR`. This is the top level data directory that all of the `watch` dirs share.  For example, if you `watch` directories are `/mnt/data/processor_1` and `/mnt/data/processor_2`, set the `DATA_DIR` to `/mnt/data`.  

## Build and run

This sets up the environment and configures the image to start on boot.

``` bash
cd /opt/storage_tools_device
. env.sh
docker compose up --build
```

## Stop

``` bash
cd /opt/storage_tools_device
. env.sh
docker compose down
```

## Artifacts

The Storage Tools Device creates additional files to speed up processing.  These will always in the form of `{original_file_name}.md5` and `{original_file_name}.metadata`. These files will only be created in the `watch` directories as defined by the config yaml file.  These files can be safely removed. They will be regenerated when the system scans again.

## Troubleshooting

[Troubleshooting](docs/Troubleshooting.md)
