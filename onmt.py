import argparse
import json
import os
import yaml

from importlib import import_module

import tensorflow as tf
import opennmt as onmt

def load_config_module(path):
  """Loads a configuration file.

  Args:
    path: The relative path to the configuration file.

  Returns:
    A Python module.
  """
  module, _ = path.rsplit(".", 1)
  module = module.replace("/", ".")
  module = import_module(module)

  if not hasattr(module, "model"):
    raise ImportError("No model defined in " + path)

  return module

def main():
  parser = argparse.ArgumentParser(description="OpenNMT-tf.")
  parser.add_argument("--run", required=True,
                      help="run configuration file")
  parser.add_argument("--model", required=True,
                      help="model configuration file")
  parser.add_argument("--task_type", default="worker", choices=["master", "worker", "ps"],
                      help="type of task to run")
  parser.add_argument("--task_id", type=int, default=0,
                      help="id of the task")
  args = parser.parse_args()

  # Load run configuration.
  with open(args.run) as config_file:
    config = yaml.load(config_file.read())

  # Setup cluster if defined.
  if "hosts" in config:
    cluster = {
      "ps": config["hosts"]["ps"],
      "worker": config["hosts"]["workers"],
      "master": config["hosts"]["masters"]
    }

    os.environ["TF_CONFIG"] = json.dumps({
      "cluster": cluster,
      "task": {"type": args.task_type, "index": args.task_id},
      "environment": "cloud"
    })

  session_config = tf.ConfigProto()
  session_config.gpu_options.allow_growth = config.get("gpu_allow_growth", False)

  run_config = tf.contrib.learn.RunConfig(
    save_summary_steps=config["run"].get("save_summary_steps", 100),
    save_checkpoints_secs=None,
    save_checkpoints_steps=config["run"].get("save_checkpoints_steps", 1000),
    keep_checkpoint_max=config["run"].get("keep_checkpoint_max", 5),
    log_step_count_steps=config["run"].get("save_summary_steps", 100),
    model_dir=config["run"]["model_dir"],
    session_config=session_config)

  params = config.get("params", {})
  params["log_dir"] = config["run"]["model_dir"]

  eval_every = config["run"].get("eval_steps")
  buffer_size = config["data"].get("buffer_size", 10000)
  num_buckets = config["data"].get("num_buckets", 5)

  # Load model configuration.
  model_config = load_config_module(args.model)
  model = model_config.model()

  estimator = tf.estimator.Estimator(
    model_fn=model,
    config=run_config,
    params=params)

  if config["run"]["type"] == "train":
    model_config.train(model)

    train_input_fn = model.input_fn(
      tf.estimator.ModeKeys.TRAIN,
      config["params"]["batch_size"],
      buffer_size,
      num_buckets,
      config["data"]["train_features_file"],
      labels_file=config["data"]["train_labels_file"])
    eval_input_fn = model.input_fn(
      tf.estimator.ModeKeys.EVAL,
      config["params"]["batch_size"],
      buffer_size,
      num_buckets,
      config["data"]["eval_features_file"],
      labels_file=config["data"]["eval_labels_file"])

    experiment = tf.contrib.learn.Experiment(
      estimator=estimator,
      train_input_fn=train_input_fn,
      eval_input_fn=eval_input_fn,
      min_eval_frequency=eval_every)

    if args.task_type == "ps":
      experiment.run_std_server()
    elif run_config.is_chief:
      experiment.train_and_evaluate()
    else:
      experiment.train()
  else:
    model_config.infer(model)

    test_input_fn = model.input_fn(
      tf.estimator.ModeKeys.PREDICT,
      config["params"]["batch_size"],
      buffer_size,
      num_buckets,
      config["data"]["features_file"],
      labels_file=config["data"].get("labels_file"))

    for predictions in estimator.predict(input_fn=test_input_fn):
      predictions = model.format_prediction(predictions, params=params)
      if not isinstance(predictions, list):
        predictions = [ predictions ]
      for prediction in predictions:
        print(prediction)

if __name__ == "__main__":
  main()